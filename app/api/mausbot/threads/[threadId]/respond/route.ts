import { NextRequest, NextResponse } from "next/server";
import { proxyJson } from "../../../_upstream";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

const behaviors = new Set(["allow", "deny", "answer"]);

function errorDetail(payload: unknown, raw: string): string {
  if (payload && typeof payload === "object" && "detail" in payload) {
    const detail = (payload as { detail?: unknown }).detail;
    if (typeof detail === "string" && detail) return detail;
  }
  return raw || "upstream error";
}

export async function POST(req: NextRequest, ctx: { params: Promise<{ threadId: string }> }) {
  let body: Record<string, unknown>;
  try {
    body = await req.json();
  } catch {
    return NextResponse.json({ ok: false, detail: "invalid json" }, { status: 400 });
  }
  const requestId = typeof body.requestId === "string" ? body.requestId.trim() : "";
  const behavior = typeof body.behavior === "string" ? body.behavior : "";
  const message = typeof body.message === "string" ? body.message.trim() : undefined;
  if (!requestId || requestId.length > 160 || !behaviors.has(behavior)) {
    return NextResponse.json({ ok: false, detail: "invalid response" }, { status: 400 });
  }
  if (message && message.length > 2000) {
    return NextResponse.json({ ok: false, detail: "message too long" }, { status: 400 });
  }
  if (behavior === "answer" && !message) {
    return NextResponse.json({ ok: false, detail: "message required for answer" }, { status: 400 });
  }
  const out: Record<string, unknown> = { requestId, behavior };
  if (message) out.message = message;
  if (typeof body.always === "boolean") out.always = body.always;
  if (typeof body.rememberCommand === "boolean") out.rememberCommand = body.rememberCommand;
  if (typeof body.reviewedSha256 === "string" && body.reviewedSha256.trim()) {
    out.reviewedSha256 = body.reviewedSha256.trim().slice(0, 128);
  }
  if (body.alwaysAllow && typeof body.alwaysAllow === "object") {
    out.alwaysAllow = body.alwaysAllow;
  }
  const { threadId } = await ctx.params;
  try {
    const { upstream, payload, raw } = await proxyJson(
      `/api/mausbot/threads/${encodeURIComponent(threadId)}/respond`,
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(out),
        timeoutMs: 30_000,
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
