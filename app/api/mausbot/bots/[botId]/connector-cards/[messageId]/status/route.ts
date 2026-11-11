import { NextRequest, NextResponse } from "next/server";
import { proxyJson } from "../../../../../_upstream";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

export async function GET(req: NextRequest, ctx: { params: Promise<{ botId: string; messageId: string }> }) {
  const { botId, messageId } = await ctx.params;
  const threadId = (req.nextUrl.searchParams.get("threadId") || "").trim();
  if (!threadId || threadId.length > 160) {
    return NextResponse.json({ ok: false, detail: "threadId required" }, { status: 400 });
  }
  try {
    const { upstream, payload, raw } = await proxyJson(
      `/api/mausbot/bots/${encodeURIComponent(botId)}/connector-cards/${encodeURIComponent(messageId)}/status?threadId=${encodeURIComponent(threadId)}`,
      { method: "GET", timeoutMs: 30000 },
    );
    if (!upstream.ok) {
      const detail = payload && typeof payload === "object" && "detail" in payload ? String((payload as { detail: unknown }).detail) : raw || "upstream error";
      return NextResponse.json({ ok: false, detail }, { status: upstream.status });
    }
    return NextResponse.json(payload ?? { ok: true }, { status: 200 });
  } catch (error) {
    const detail = error instanceof Error ? error.message : "MausBot proxy unreachable";
    return NextResponse.json({ ok: false, detail }, { status: 503 });
  }
}
