import { NextRequest } from "next/server";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

const UPSTREAM = process.env.MAUSBOT_PROXY_URL || "http://127.0.0.1:18800";

export async function POST(req: NextRequest) {
  let body: { text?: string; botId?: string; threadId?: string };
  try {
    body = await req.json();
  } catch {
    return new Response(JSON.stringify({ ok: false, detail: "invalid json" }), {
      status: 400,
      headers: { "Content-Type": "application/json" },
    });
  }
  const text = typeof body.text === "string" ? body.text.trim() : "";
  if (!text) {
    return new Response(JSON.stringify({ ok: false, detail: "text required" }), {
      status: 400,
      headers: { "Content-Type": "application/json" },
    });
  }
  if (text.length > 2000) {
    return new Response(JSON.stringify({ ok: false, detail: "text too long" }), {
      status: 400,
      headers: { "Content-Type": "application/json" },
    });
  }

  let upstream: Response;
  try {
    upstream = await fetch(`${UPSTREAM}/api/mausbot/ask/stream`, {
      method: "POST",
      headers: { "Content-Type": "application/json", Accept: "text/event-stream" },
      body: JSON.stringify({
        text,
        botId: typeof body.botId === "string" ? body.botId : undefined,
        threadId: typeof body.threadId === "string" ? body.threadId : undefined,
      }),
      cache: "no-store",
      signal: req.signal,
    });
  } catch (error) {
    const detail = error instanceof Error ? error.message : "MausBot proxy unreachable";
    return new Response(JSON.stringify({ ok: false, detail }), {
      status: 503,
      headers: { "Content-Type": "application/json" },
    });
  }

  if (!upstream.ok || !upstream.body) {
    const raw = await upstream.text().catch(() => "");
    let detail = raw || `MausBot proxy error (${upstream.status})`;
    try {
      const parsed = JSON.parse(raw);
      if (parsed?.detail) detail = String(parsed.detail);
    } catch {
      /* keep */
    }
    return new Response(JSON.stringify({ ok: false, detail }), {
      status: upstream.status || 502,
      headers: { "Content-Type": "application/json" },
    });
  }

  return new Response(upstream.body, {
    status: 200,
    headers: {
      "Content-Type": "text/event-stream; charset=utf-8",
      "Cache-Control": "no-cache, no-transform",
      Connection: "keep-alive",
      "X-Accel-Buffering": "no",
    },
  });
}
