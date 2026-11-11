import { NextRequest, NextResponse } from "next/server";
import { proxyJson } from "../../../../../_upstream";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

function errorDetail(payload: unknown, raw: string): string {
  if (payload && typeof payload === "object" && "detail" in payload) {
    const detail = (payload as { detail?: unknown }).detail;
    if (typeof detail === "string" && detail) return detail;
  }
  return raw || "upstream error";
}

export async function POST(req: NextRequest, ctx: { params: Promise<{ botId: string; messageId: string }> }) {
  let body: Record<string, unknown>;
  try {
    body = await req.json();
  } catch {
    return NextResponse.json({ ok: false, detail: "invalid json" }, { status: 400 });
  }
  const threadId = typeof body.threadId === "string" ? body.threadId.trim() : "";
  if (!threadId || threadId.length > 160) {
    return NextResponse.json({ ok: false, detail: "threadId required" }, { status: 400 });
  }
  const { botId, messageId } = await ctx.params;
  const payload: Record<string, unknown> = { threadId };
  if (typeof body.alias === "string" && body.alias.trim()) payload.alias = body.alias.trim().slice(0, 128);
  if (Array.isArray(body.content)) payload.content = body.content.slice(0, 40);
  if (typeof body.method === "string" && body.method.trim()) payload.method = body.method.trim().slice(0, 80);
  if (body.params && typeof body.params === "object") payload.params = body.params;
  if (typeof body.confirmed === "boolean") payload.confirmed = body.confirmed;
  try {
    const { upstream, payload: resp, raw } = await proxyJson(
      `/api/mausbot/bots/${encodeURIComponent(botId)}/sign-in-cards/${encodeURIComponent(messageId)}/cancel`,
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
        timeoutMs: 60000,
      },
    );
    if (!upstream.ok) {
      return NextResponse.json({ ok: false, detail: errorDetail(resp, raw) }, { status: upstream.status });
    }
    return NextResponse.json(resp ?? { ok: true }, { status: 200 });
  } catch (error) {
    const detail = error instanceof Error ? error.message : "MausBot proxy unreachable";
    return NextResponse.json({ ok: false, detail }, { status: 503 });
  }
}
