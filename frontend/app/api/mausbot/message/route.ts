import { NextRequest, NextResponse } from "next/server";
import { proxyJson } from "../_upstream";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

export async function POST(req: NextRequest) {
  let body: { text?: string; botId?: string; threadId?: string };
  try {
    body = await req.json();
  } catch {
    return NextResponse.json({ ok: false, detail: "invalid json" }, { status: 400 });
  }
  const text = typeof body.text === "string" ? body.text.trim() : "";
  if (!text) return NextResponse.json({ ok: false, detail: "text required" }, { status: 400 });
  if (text.length > 2000) return NextResponse.json({ ok: false, detail: "text too long" }, { status: 400 });

  try {
    const { upstream, raw, payload } = await proxyJson("/api/mausbot/message", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        text,
        botId: typeof body.botId === "string" ? body.botId : undefined,
        threadId: typeof body.threadId === "string" ? body.threadId : undefined,
      }),
      timeoutMs: 30_000,
    });
    if (!upstream.ok) {
      const detail =
        payload && typeof payload === "object" && payload !== null && "detail" in payload
          ? String((payload as { detail: unknown }).detail)
          : raw || `MausBot proxy error (${upstream.status})`;
      return NextResponse.json({ ok: false, detail }, { status: upstream.status });
    }
    return NextResponse.json(payload, { status: 200 });
  } catch (error) {
    const detail = error instanceof Error ? error.message : "MausBot proxy unreachable";
    return NextResponse.json({ ok: false, detail }, { status: 503 });
  }
}
