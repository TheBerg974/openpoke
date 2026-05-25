export const runtime = 'nodejs';

type UIMsgPart = { type: string; text?: string };
type UIMessage = { role: string; parts?: UIMsgPart[]; content?: string };

function uiToOpenAIContent(messages: UIMessage[]): { role: string; content: string }[] {
  const out: { role: string; content: string }[] = [];
  for (const m of messages || []) {
    const role = m?.role;
    if (!role) continue;
    let content = '';
    if (Array.isArray(m.parts)) {
      content = m.parts.filter((p) => p?.type === 'text').map((p) => p.text || '').join('');
    } else if (typeof m.content === 'string') {
      content = m.content;
    }
    out.push({ role, content });
  }
  return out;
}

export async function POST(req: Request) {
  let body: any;
  try {
    body = await req.json();
  } catch (e) {
    console.error('[chat-proxy] invalid json', e);
    return new Response('Invalid JSON', { status: 400 });
  }

  // ── APM mode ──────────────────────────────────────────────────────────────
  if (body?.mode === 'apm') {
    const { message, user_id, thread_id } = body;
    if (!message || !user_id) {
      return new Response('Missing message or user_id', { status: 400 });
    }

    const apmBase = process.env.APM_SERVER_URL || 'http://localhost:8002';
    const url = `${apmBase.replace(/\/$/, '')}/api/v1/apm/chat`;

    const payload: Record<string, string> = { user_id, message };
    if (thread_id) payload.thread_id = thread_id;

    try {
      const upstream = await fetch(url, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
      });

      if (!upstream.ok) {
        const errText = await upstream.text();
        console.error('[apm-proxy] upstream error', upstream.status, errText);
        return new Response(errText || 'APM upstream error', { status: upstream.status });
      }

      const data = await upstream.json();
      // Return the reply as plain text (matches existing UI expectations)
      // Also pass thread_id back via header so the client can persist it
      return new Response(data.reply || '', {
        status: 200,
        headers: {
          'Content-Type': 'text/plain; charset=utf-8',
          'X-Thread-Id': data.thread_id || '',
        },
      });
    } catch (e: any) {
      console.error('[apm-proxy] fetch error', e);
      return new Response(e?.message || 'APM proxy error', { status: 502 });
    }
  }

  // ── Base mode (OpenRouter) ─────────────────────────────────────────────────
  const { messages } = body || {};
  if (!Array.isArray(messages) || messages.length === 0) {
    return new Response('Missing messages', { status: 400 });
  }

  const serverBase = process.env.PY_SERVER_URL || 'http://localhost:8001';
  const serverPath = process.env.PY_CHAT_PATH || '/api/v1/chat/send';
  const url = `${serverBase.replace(/\/$/, '')}${serverPath}`;

  const payload = {
    system: '',
    messages: uiToOpenAIContent(messages),
    stream: false,
  };

  try {
    const upstream = await fetch(url, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', Accept: 'text/plain, */*' },
      body: JSON.stringify(payload),
    });
    const text = await upstream.text();
    return new Response(text, {
      status: upstream.status,
      headers: { 'Content-Type': 'text/plain; charset=utf-8' },
    });
  } catch (e: any) {
    console.error('[chat-proxy] upstream error', e);
    return new Response(e?.message || 'Upstream error', { status: 502 });
  }
}
