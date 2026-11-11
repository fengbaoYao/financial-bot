import { NextRequest, NextResponse } from "next/server";
import { proxyJson } from "../../../../_upstream";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

function errorDetail(payload: unknown, raw: string): string {
  if (payload && typeof payload === "object" && "detail" in payload) {
    const detail = (payload as { detail?: unknown }).detail;
    if (typeof detail === "string" && detail) return detail;
  }
  return raw || "upstream error";
}

export async function PATCH(req: NextRequest, ctx: { params: Promise<{ botId: string; threadId: string }> }) {
  let body: Record<string, unknown>;
  try {
    body = await req.json();
  } catch {
    return NextResponse.json({ ok: false, detail: "invalid json" }, { status: 400 });
  }
  const out: Record<string, unknown> = {};
  if (typeof body.title === "string") {
    const title = body.title.trim();
    if (!title) return NextResponse.json({ ok: false, detail: "title required" }, { status: 400 });
    out.title = title.slice(0, 200);
  }
  if (typeof body.pinned === "boolean") out.pinned = body.pinned;
  if (body.archivedAt === null) out.clearArchived = true;
  else if (typeof body.archivedAt === "number" && Number.isFinite(body.archivedAt)) out.archivedAt = body.archivedAt;
  if (!Object.keys(out).length) {
    return NextResponse.json({ ok: false, detail: "no supported fields" }, { status: 400 });
  }
  const { botId, threadId } = await ctx.params;
  try {
    const { upstream, payload, raw } = await proxyJson(
      `/api/mausbot/bots/${encodeURIComponent(botId)}/tasks/${encodeURIComponent(threadId)}`,
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

export async function DELETE(_req: NextRequest, ctx: { params: Promise<{ botId: string; threadId: string }> }) {
  const { botId, threadId } = await ctx.params;
  try {
    const { upstream, payload, raw } = await proxyJson(
      `/api/mausbot/bots/${encodeURIComponent(botId)}/tasks/${encodeURIComponent(threadId)}`,
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
