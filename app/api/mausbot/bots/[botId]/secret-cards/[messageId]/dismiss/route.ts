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
  let body: { threadId?: unknown; value?: unknown };
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
  try {
    const { upstream, payload, raw } = await proxyJson(
      `/api/mausbot/bots/${encodeURIComponent(botId)}/secret-cards/${encodeURIComponent(messageId)}/dismiss`,
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ threadId }),
        timeoutMs: 30000,
      },
    );
    if (!upstream.ok) {
      return NextResponse.json({ ok: false, detail: errorDetail(payload, raw) }, { status: upstream.status });
    }
    return NextResponse.json(payload ?? { ok: true }, { status: 200 });
  } catch (error) {
    const detail = error instanceof Error ? error.message : "MausBot proxy unreachable";
    return NextResponse.json({ ok: false, detail }, { status: 503 });
  }
}
