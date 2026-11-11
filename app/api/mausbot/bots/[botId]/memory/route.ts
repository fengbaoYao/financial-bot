import { NextResponse } from "next/server";
import { proxyJson } from "../../../_upstream";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

function errorDetail(payload: unknown, raw: string): string {
  if (payload && typeof payload === "object" && "detail" in payload) {
    const detail = (payload as { detail?: unknown }).detail;
    if (typeof detail === "string" && detail) return detail;
  }
  return raw || "upstream error";
}

export async function GET(_req: Request, ctx: { params: Promise<{ botId: string }> }) {
  const { botId } = await ctx.params;
  try {
    const { upstream, payload, raw } = await proxyJson(`/api/mausbot/bots/${encodeURIComponent(botId)}/memory`, {
      timeoutMs: 20_000,
    });
    if (!upstream.ok) {
      return NextResponse.json(
        { ok: false, detail: errorDetail(payload, raw) },
        { status: upstream.status },
      );
    }
    return NextResponse.json(payload, { status: 200 });
  } catch (error) {
    const detail = error instanceof Error ? error.message : "MausBot proxy unreachable";
    return NextResponse.json({ ok: false, detail }, { status: 503 });
  }
}
