import { NextRequest, NextResponse } from "next/server";
import { proxyJson } from "../_upstream";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

export async function POST(req: NextRequest) {
  let body: { botId?: string; threadId?: string; turnId?: string };
  try {
    body = await req.json();
  } catch {
    return NextResponse.json({ ok: false, detail: "invalid json" }, { status: 400 });
  }
  const threadId = typeof body.threadId === "string" ? body.threadId.trim() : "";
  if (!threadId) return NextResponse.json({ ok: false, detail: "threadId required" }, { status: 400 });

  try {
    const { upstream, raw, payload } = await proxyJson("/api/mausbot/interrupt", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        botId: typeof body.botId === "string" ? body.botId : undefined,
        threadId,
        turnId: typeof body.turnId === "string" ? body.turnId : undefined,
      }),
      timeoutMs: 30_000,
    });
    // Idle interrupt may be non-2xx from upstream; still return body so the shell can proceed.
    if (!upstream.ok) {
      const detail =
        payload && typeof payload === "object" && payload !== null
          ? payload
          : { ok: false, detail: raw || `MausBot proxy error (${upstream.status})` };
      return NextResponse.json(detail, { status: upstream.status || 502 });
    }
    return NextResponse.json(payload, { status: 200 });
  } catch (error) {
    const detail = error instanceof Error ? error.message : "MausBot proxy unreachable";
    return NextResponse.json({ ok: false, detail }, { status: 503 });
  }
}
