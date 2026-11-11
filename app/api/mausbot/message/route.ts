import { NextRequest, NextResponse } from "next/server";
import { proxyJson } from "../_upstream";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

export async function POST(req: NextRequest) {
  let body: Record<string, unknown>;
  try {
    body = await req.json();
  } catch {
    return NextResponse.json({ ok: false, detail: "invalid json" }, { status: 400 });
  }
  const text = typeof body.text === "string" ? body.text.trim() : "";
  const hasWidget = body.widgetAction && typeof body.widgetAction === "object";
  const hasApp = body.appMessage && typeof body.appMessage === "object";
  if (!text && !hasWidget && !hasApp) {
    return NextResponse.json({ ok: false, detail: "text required" }, { status: 400 });
  }
  if (text.length > 2000) return NextResponse.json({ ok: false, detail: "text too long" }, { status: 400 });

  const payload: Record<string, unknown> = {};
  if (text) payload.text = text;
  if (typeof body.botId === "string") payload.botId = body.botId;
  if (typeof body.threadId === "string") payload.threadId = body.threadId;
  if (typeof body.sendId === "string") payload.sendId = body.sendId;
  if (hasWidget) payload.widgetAction = body.widgetAction;
  if (hasApp) payload.appMessage = body.appMessage;

  try {
    const { upstream, raw, payload: resp } = await proxyJson("/api/mausbot/message", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
      timeoutMs: 30_000,
    });
    if (!upstream.ok) {
      const detail =
        resp && typeof resp === "object" && resp !== null && "detail" in resp
          ? String((resp as { detail: unknown }).detail)
          : raw || `MausBot proxy error (${upstream.status})`;
      return NextResponse.json({ ok: false, detail }, { status: upstream.status });
    }
    return NextResponse.json(resp, { status: 200 });
  } catch (error) {
    const detail = error instanceof Error ? error.message : "MausBot proxy unreachable";
    return NextResponse.json({ ok: false, detail }, { status: 503 });
  }
}
