# OpenPoke 🌴

OpenPoke is a simplified, open-source take on [Interaction Company’s](https://interaction.co/about) [Poke](https://poke.com/) assistant—built to show how a multi-agent orchestration stack can feel genuinely useful. It keeps the handful of things Poke is great at (email triage, reminders, and persistent agents) while staying easy to spin up locally.

- Multi-agent FastAPI backend that mirrors Poke's interaction/execution split, powered by [OpenRouter](https://openrouter.ai/).
- Gmail tooling via [Composio](https://composio.dev/) for drafting/replying/forwarding without leaving chat.
- Trigger scheduler and background watchers for reminders and "important email" alerts.
- Next.js web UI that proxies everything through the shared `.env`, so plugging in API keys is the only setup.

## Requirements
- Python 3.10+
- Node.js 18+
- npm 9+

## Quickstart
1. **Clone and enter the repo.**
   ```bash
   git clone https://github.com/shlokkhemani/OpenPoke
   cd OpenPoke
   ```
2. **Create a shared env file.** Copy the template and open it in your editor:
   ```bash
   cp .env.example .env
   ```
3. **Get your API keys and add them to `.env`:**
   
   **OpenRouter (Required)**
   - Create an account at [openrouter.ai](https://openrouter.ai/)
   - Generate an API key
   - Replace `your_openrouter_api_key_here` with your actual key in `.env`
   
   **Composio (Required for Gmail)**
   - Sign in at [composio.dev](https://composio.dev/)
   - Create an API key
   - Set up Gmail integration and get your auth config ID
   - Replace `your_composio_api_key_here` and `your_gmail_auth_config_id_here` in `.env`
4. **(Required) Create and activate a Python 3.10+ virtualenv:**
   ```bash
   # Ensure you're using Python 3.10+
   python3.10 -m venv .venv
   source .venv/bin/activate
   
   # Verify Python version (should show 3.10+)
   python --version
   ```
   On Windows (PowerShell):
   ```powershell
   # Use Python 3.10+ (adjust path as needed)
   python3.10 -m venv .venv
   .\.venv\Scripts\Activate.ps1
   
   # Verify Python version
   python --version
   ```

5. **Install backend dependencies:**
   ```bash
   pip install -r server/requirements.txt
   ```
6. **Install frontend dependencies:**
   ```bash
   npm install --prefix web
   ```
7. **Start the FastAPI server:**
   ```bash
   python -m server.server --reload
   ```
8. **Start the Next.js app (new terminal):**
   ```bash
   npm run dev --prefix web
   ```
9. **Connect Gmail for email workflows.** With both services running, open [http://localhost:3000](http://localhost:3000), head to *Settings → Gmail*, and complete the Composio OAuth flow. This step is required for email drafting, replies, and the important-email monitor.

The web app proxies API calls to the Python server using the values in `.env`, so keeping both processes running is required for end-to-end flows.

## Project Layout
- `server/` – FastAPI application and agents
- `web/` – Next.js app
- `server/data/` – runtime data (ignored by git)

---

## APM Layer — Local LangGraph Orchestration

This fork adds a self-contained **Agent Package Manager (APM)** layer on top of the existing OpenPoke server. It introduces a second chat endpoint that routes messages through a local LangGraph pipeline instead of OpenRouter, and persists thread state to PostgreSQL with a Redis L1 cache.

### New endpoint

```
POST /api/v1/apm/chat
```

```json
// Request
{ "user_id": "alice", "thread_id": "optional-uuid", "message": "Draft an email to ash@pokemon.com..." }

// Response
{ "thread_id": "93860662-...", "reply": "Subject: ..." }
```

Omit `thread_id` to start a new thread. Pass it back on subsequent requests to resume.

### Architecture

```
User message
     │
     ▼
┌────────────────────────────────────────────────────┐
│                   LangGraph pipeline                │
│                                                    │
│  GlobalRouter ──► SubAgentExecutor ──► ContextPruner ──► reply
│      │                   │                   │           │
│  (picks agent)    (calls executor)    (strips raw   (plain text
│                                        tool output)  to client)
└────────────────────────────────────────────────────┘
     │                                          │
     ▼                                          ▼
Redis L1 cache                          PostgreSQL
(thread state, 30 min TTL)          (thread_meta + thread_history)
```

Each installed APM agent lives in `apm_modules/<owner>/<package>/agents/<name>/` and ships two files:
- `agent.json` — schema (name, description, parameters)
- `executor.py` — `execute(params) -> dict` implementation

### Solving system-prompt overloading

Multi-agent systems have a structural problem: as threads grow, naively concatenating every agent's system prompt plus the full message history floods the context window with competing instructions. This causes routing errors, tool-argument hallucinations, and latency spikes.

The APM layer addresses this with four mechanisms:

| Mechanism | How it works |
|-----------|-------------|
| **Node isolation** | GlobalRouter, SubAgentExecutor, and ContextPruner each make their own LLM call with a short, focused system prompt (~50–120 tokens). No node sees another node's instructions. |
| **Typed state signals** | Nodes communicate through typed fields (`active_tools`, `_raw_tool_output`) rather than injecting context into the message list. |
| **Ephemeral output stripping** | ContextPruner deletes `_raw_tool_output` from state after formatting the reply. Large payloads never accumulate in thread history. |
| **Selective history hydration** | Only the last K messages from PostgreSQL are loaded per turn. Redis caches the hot state so DB reads are rare. |

See `experiments/overload_test.py` for empirical tests that measure these effects.

### APM quick start

```bash
# 1. Start infrastructure (Postgres + Redis)
docker compose up -d

# 2. Install APM agents
apm install TheBerg974/open-poke-agents --target copilot

# 3. Copy env and fill in LLM provider
cp .env.example .env
# set LLM_PROVIDER=ollama (or gemini / openai) in .env

# 4. Activate venv and start server
source .venv/bin/activate
python -m uvicorn server.app:app --reload --port 8002

# 5. Test the APM endpoint
curl -X POST http://localhost:8002/api/v1/apm/chat \
  -H "Content-Type: application/json" \
  -d '{"user_id":"test","message":"What is the latest news about Pokemon?"}'
```

### Local LLM providers

| Provider | `.env` settings | Notes |
|----------|----------------|-------|
| **Ollama** (recommended) | `LLM_PROVIDER=ollama`<br>`OLLAMA_MODEL=qwen2.5:14b` | Free, runs locally. Install: `brew install ollama && ollama pull qwen2.5:14b` |
| **Gemini** | `LLM_PROVIDER=gemini`<br>`GOOGLE_API_KEY=...` | Free tier available at aistudio.google.com |
| **OpenAI** | `LLM_PROVIDER=openai`<br>`OPENAI_API_KEY=...` | GPT-4o-mini recommended |

### Project layout (updated)

```
server/
  apm/
    __init__.py
    agent_loader.py   ← discovers + loads APM packages from apm_modules/
    cache.py          ← Redis L1 (30 min TTL, silent failover)
    database.py       ← SQLAlchemy 2 async ORM (ThreadMeta, ThreadHistory)
    graph.py          ← LangGraph StateGraph (GlobalRouter → SubAgentExecutor → ContextPruner)
  routes/
    apm.py            ← POST /api/v1/apm/chat
    chat.py           ← existing POST /api/v1/chat/send (OpenRouter + Composio)
apm_modules/          ← gitignored; populated by `apm install`
apm.yml              ← pins agent packages
experiments/
  overload_test.py    ← empirical tests for system-prompt overloading
docker-compose.yml    ← PostgreSQL 15 + Redis Stack
```

---

## License
MIT — see [LICENSE](LICENSE).
