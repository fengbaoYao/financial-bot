import { NextRequest, NextResponse } from "next/server";
import { proxyJson } from "../../_upstream";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

function errorDetail(payload: unknown, raw: string): string {
  if (payload && typeof payload === "object" && "detail" in payload) {
    const detail = (payload as { detail?: unknown }).detail;
    if (typeof detail === "string" && detail) return detail;
  }
  return raw || "upstream error";
}

export async function PATCH(req: NextRequest, ctx: { params: Promise<{ routineId: string }> }) {
  let body: { enabled?: unknown; name?: unknown };
  try {
    body = await req.json();
  } catch {
    return NextResponse.json({ ok: false, detail: "invalid json" }, { status: 400 });
  }
  const out: Record<string, unknown> = {};
  if (typeof body.enabled === "boolean") out.enabled = body.enabled;
  if (typeof body.name === "string" && body.name.trim()) out.name = body.name.trim().slice(0, 200);
  if (!Object.keys(out).length) {
    return NextResponse.json({ ok: false, detail: "no supported fields" }, { status: 400 });
  }
  const { routineId } = await ctx.params;
  try {
    const { upstream, payload, raw } = await proxyJson(
      `/api/mausbot/routines/${encodeURIComponent(routineId)}`,
      {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(out),
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

export async function DELETE(_req: NextRequest, ctx: { params: Promise<{ routineId: string }> }) {
  const { routineId } = await ctx.params;
  try {
    const { upstream, payload, raw } = await proxyJson(
      `/api/mausbot/routines/${encodeURIComponent(routineId)}`,
      { method: "DELETE", timeoutMs: 30000 },
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
