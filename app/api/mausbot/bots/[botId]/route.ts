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

export async function PATCH(req: NextRequest, ctx: { params: Promise<{ botId: string }> }) {
  let body: Record<string, unknown>;
  try {
    body = await req.json();
  } catch {
    return NextResponse.json({ ok: false, detail: "invalid json" }, { status: 400 });
  }
  const out: Record<string, unknown> = {};
  if (body.clearComputer === true) out.clearComputer = true;
  else if (body.computer === null) out.clearComputer = true;
  else if (typeof body.computer === "string") out.computer = body.computer;
  const ms = body.modelSelection;
  if (ms && typeof ms === "object") {
    const rec = ms as Record<string, unknown>;
    const instanceId = typeof rec.instanceId === "string" ? rec.instanceId.trim() : "";
    const model = typeof rec.model === "string" ? rec.model.trim() : "";
    if (instanceId && model) {
      const selection: Record<string, string> = { instanceId, model };
      if (typeof rec.effort === "string" && rec.effort.trim()) selection.effort = rec.effort.trim();
      out.modelSelection = selection;
    }
  }
  if (typeof body.threadId === "string" && body.threadId.trim()) out.threadId = body.threadId.trim();
  if (!Object.keys(out).length) {
    return NextResponse.json({ ok: false, detail: "no supported fields" }, { status: 400 });
  }
  const { botId } = await ctx.params;
  try {
    const { upstream, payload, raw } = await proxyJson(`/api/mausbot/bots/${encodeURIComponent(botId)}`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(out),
      timeoutMs: 30000,
    });
    if (!upstream.ok) {
      return NextResponse.json({ ok: false, detail: errorDetail(payload, raw) }, { status: upstream.status });
    }
    return NextResponse.json(payload ?? { ok: true }, { status: 200 });
  } catch (error) {
    const detail = error instanceof Error ? error.message : "MausBot proxy unreachable";
    return NextResponse.json({ ok: false, detail }, { status: 503 });
  }
}
