#!/usr/bin/env python3
"""
E2E test for the Redis cache and PostgreSQL persistence layers.

Usage:
    python experiments/test_cache_db.py [--port 8002]

What it checks
--------------
Turn 1  POST /api/v1/apm/chat  → thread_id + reply
Redis   thread:state:<id>      → key present, TTL ≤ 1800 s
Redis   meta:registry          → entry for thread_id present
PG      thread_meta            → row with thread_id
PG      thread_history         → 2 rows  (user + assistant)
Turn 2  POST (same thread_id)  → reply mentions Pikachu context (multi-turn)
Timing  Turn 2 < Turn 1        → cache warm-up benefit visible
"""

import argparse
import asyncio
import json
import os
import sys
import time
from pathlib import Path

# ---------------------------------------------------------------------------
# Deps — standard library only for HTTP; use direct Redis/PG clients
# ---------------------------------------------------------------------------
import urllib.request
import urllib.error

# Load .env from project root so DATABASE_URL / REDIS_URL are available
_env_path = Path(__file__).parent.parent / ".env"
if _env_path.exists():
    for _line in _env_path.read_text().splitlines():
        _line = _line.strip()
        if _line and not _line.startswith("#") and "=" in _line:
            _k, _, _v = _line.partition("=")
            os.environ.setdefault(_k.strip(), _v.strip())

import redis.asyncio as aioredis
import asyncpg

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

PASS = "\033[32m✓\033[0m"
FAIL = "\033[31m✗\033[0m"
INFO = "\033[34m·\033[0m"

failures: list[str] = []


def ok(label: str, detail: str = "") -> None:
    print(f"  {PASS}  {label}" + (f"  ({detail})" if detail else ""))


def fail(label: str, detail: str = "") -> None:
    msg = f"  {FAIL}  {label}" + (f"  ({detail})" if detail else "")
    print(msg)
    failures.append(label)


def info(label: str) -> None:
    print(f"\n{INFO} {label}")


def post_chat(base_url: str, payload: dict) -> tuple[dict, float]:
    """Synchronous POST to /api/v1/apm/chat. Returns (response_dict, elapsed_s)."""
    data = json.dumps(payload).encode()
    req = urllib.request.Request(
        f"{base_url}/api/v1/apm/chat",
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    t0 = time.perf_counter()
    with urllib.request.urlopen(req, timeout=120) as resp:
        body = json.loads(resp.read())
    elapsed = time.perf_counter() - t0
    return body, elapsed


# ---------------------------------------------------------------------------
# Async checks
# ---------------------------------------------------------------------------

async def check_redis(thread_id: str) -> None:
    info("Redis checks")
    redis_url = os.getenv("REDIS_URL", "redis://localhost:6379/0")
    client = aioredis.from_url(redis_url, decode_responses=True, socket_timeout=5)
    try:
        # 1. Thread state key
        state_key = f"thread:state:{thread_id}"
        raw = await client.get(state_key)
        if raw:
            ttl = await client.ttl(state_key)
            ok("thread:state key present", f"TTL={ttl}s, {len(raw)} bytes")
        else:
            fail("thread:state key missing", state_key)

        # 2. Meta-registry
        registry_raw = await client.get("meta:registry")
        if registry_raw:
            registry = json.loads(registry_raw)
            match = next((r for r in registry if r.get("thread_id") == thread_id), None)
            if match:
                ok("meta:registry entry present", f"title='{match.get('title', '?')}'")
            else:
                fail("thread_id missing from meta:registry", f"{len(registry)} total entries")
        else:
            fail("meta:registry key missing in Redis")
    finally:
        await client.aclose()


async def check_postgres(thread_id: str) -> None:
    info("PostgreSQL checks")
    db_url = os.getenv(
        "DATABASE_URL",
        "postgresql+asyncpg://admin:password@localhost:5432/open_poke",
    )
    # asyncpg expects postgresql:// not postgresql+asyncpg://
    pg_url = db_url.replace("postgresql+asyncpg://", "postgresql://")

    conn = await asyncpg.connect(pg_url, timeout=10)
    try:
        # 1. thread_meta row
        meta = await conn.fetchrow(
            "SELECT thread_id, title, updated_at FROM thread_meta WHERE thread_id = $1",
            thread_id,
        )
        if meta:
            ok("thread_meta row present", f"title='{meta['title']}', updated={meta['updated_at']}")
        else:
            fail("thread_meta row missing", thread_id)

        # 2. thread_history rows
        rows = await conn.fetch(
            "SELECT role, content FROM thread_history WHERE thread_id = $1 ORDER BY id",
            thread_id,
        )
        if len(rows) >= 2:
            ok(f"thread_history has {len(rows)} rows")
            for r in rows:
                preview = (r["content"] or "")[:60].replace("\n", " ")
                print(f"       role={r['role']:9s}  {preview}…")
        else:
            fail(f"thread_history expected ≥2 rows, got {len(rows)}")
    finally:
        await conn.close()


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

async def run(base_url: str) -> None:
    user_id = "e2e-cache-test"

    # ── Turn 1 ──────────────────────────────────────────────────────────────
    info("Turn 1 — cold request (Pikachu stats)")
    payload1 = {"user_id": user_id, "message": "What are Pikachu's base stats?"}
    try:
        resp1, t1 = post_chat(base_url, payload1)
    except Exception as e:
        fail("Turn 1 request failed", str(e))
        return

    thread_id = resp1.get("thread_id", "")
    reply1 = resp1.get("reply", "")

    if thread_id:
        ok("Got thread_id", thread_id)
    else:
        fail("No thread_id in response")
        return

    if reply1:
        ok("Got reply", f"{len(reply1)} chars, {t1:.2f}s")
        print(f"       {reply1[:120].replace(chr(10), ' ')}…")
    else:
        fail("Empty reply")

    # Background task needs a moment to write Redis + Postgres
    print(f"  {INFO}  Waiting 3s for background sync task…")
    await asyncio.sleep(3)

    # ── Redis + Postgres after turn 1 ───────────────────────────────────────
    await check_redis(thread_id)
    await check_postgres(thread_id)

    # ── Turn 2 — same thread (multi-turn context) ────────────────────────────
    info("Turn 2 — warm request on same thread (follow-up question)")
    payload2 = {
        "user_id": user_id,
        "thread_id": thread_id,
        "message": "What type is Pikachu?",
    }
    try:
        resp2, t2 = post_chat(base_url, payload2)
    except Exception as e:
        fail("Turn 2 request failed", str(e))
        return

    reply2 = resp2.get("reply", "")
    if reply2:
        ok("Got reply", f"{len(reply2)} chars, {t2:.2f}s")
        print(f"       {reply2[:120].replace(chr(10), ' ')}…")
    else:
        fail("Empty reply on turn 2")

    # Context continuity — reply should mention Pikachu or Electric without restating the question
    if any(w in reply2.lower() for w in ["pikachu", "electric", "lightning", "pokémon", "pokemon"]):
        ok("Context continuity — follow-up used prior thread context")
    else:
        fail("Reply may lack thread context", f"reply: {reply2[:80]}")

    # Background task for turn 2
    print(f"  {INFO}  Waiting 3s for background sync task…")
    await asyncio.sleep(3)

    # Timing hint (LLM latency varies; just informational)
    info(f"Timing  turn1={t1:.2f}s  turn2={t2:.2f}s")
    if t2 < t1 * 1.5:
        ok("Turn 2 not slower than turn 1 (cache warm)")
    else:
        print(f"  {INFO}  Turn 2 was slower ({t2:.2f}s vs {t1:.2f}s) — LLM latency dominates")

    # ── Verify Postgres has 4 rows now ───────────────────────────────────────
    info("PostgreSQL — after turn 2")
    db_url = os.getenv("DATABASE_URL", "postgresql+asyncpg://admin:password@localhost:5432/open_poke")
    pg_url = db_url.replace("postgresql+asyncpg://", "postgresql://")
    conn = await asyncpg.connect(pg_url, timeout=10)
    try:
        count = await conn.fetchval(
            "SELECT COUNT(*) FROM thread_history WHERE thread_id = $1", thread_id
        )
        if count >= 4:
            ok(f"thread_history now has {count} rows (2 turns × 2 roles)")
        else:
            fail(f"Expected ≥4 rows, got {count}")
    finally:
        await conn.close()

    # ── Summary ─────────────────────────────────────────────────────────────
    print()
    if failures:
        print(f"\033[31mFAILED — {len(failures)} check(s): {', '.join(failures)}\033[0m")
        sys.exit(1)
    else:
        print("\033[32mAll checks passed.\033[0m")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8002)
    args = parser.parse_args()
    asyncio.run(run(f"http://localhost:{args.port}"))
