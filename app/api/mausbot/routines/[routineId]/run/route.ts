import { NextRequest, NextResponse } from "next/server";
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

export async function POST(_req: NextRequest, ctx: { params: Promise<{ routineId: string }> }) {
  const { routineId } = await ctx.params;
  try {
    const { upstream, payload, raw } = await proxyJson(
      `/api/mausbot/routines/${encodeURIComponent(routineId)}/run`,
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: "{}",
        timeoutMs: 30000,
      },
    );
    if (!upstream.ok) {
      return NextResponse.json({ ok: false, detail: errorDetail(payload, raw) }, { status: upstream.status });
    }
    return NextResponse.json(payload ?? { ok: true }, { status: upstream.status || 201 });
  } catch (error) {
    const detail = error instanceof Error ? error.message : "MausBot proxy unreachable";
    return NextResponse.json({ ok: false, detail }, { status: 503 });
  }
}
