import { NextRequest, NextResponse } from "next/server";
import { proxyJson } from "../_upstream";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

function errorDetail(payload: unknown, raw: string): string {
  if (payload && typeof payload === "object" && "detail" in payload) {
    const detail = (payload as { detail?: unknown }).detail;
    if (typeof detail === "string" && detail) return detail;
  }
  return raw || "upstream error";
}

export async function GET() {
  try {
    const { upstream, payload, raw } = await proxyJson("/api/mausbot/routines", { timeoutMs: 20_000 });
    if (!upstream.ok) {
      return NextResponse.json({ ok: false, detail: errorDetail(payload, raw) }, { status: upstream.status });
    }
    return NextResponse.json(payload, { status: 200 });
  } catch (error) {
    const detail = error instanceof Error ? error.message : "MausBot proxy unreachable";
    return NextResponse.json({ ok: false, detail }, { status: 503 });
  }
}

export async function POST(req: NextRequest) {
  let body: Record<string, unknown>;
  try {
    body = await req.json();
  } catch {
    return NextResponse.json({ ok: false, detail: "invalid json" }, { status: 400 });
  }
  const name = typeof body.name === "string" ? body.name.trim() : "";
  const prompt = typeof body.prompt === "string" ? body.prompt.trim() : "";
  if (!name || !prompt) {
    return NextResponse.json({ ok: false, detail: "name and prompt required" }, { status: 400 });
  }
  const out: Record<string, unknown> = { name: name.slice(0, 200), prompt: prompt.slice(0, 4000) };
  if (typeof body.botId === "string" && body.botId.trim()) out.botId = body.botId.trim();
  if (typeof body.everyMinutes === "number") out.everyMinutes = body.everyMinutes;
  if (typeof body.at === "number") out.at = body.at;
  try {
    const { upstream, payload, raw } = await proxyJson("/api/mausbot/routines", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(out),
      timeoutMs: 30000,
    });
    if (!upstream.ok) {
      return NextResponse.json({ ok: false, detail: errorDetail(payload, raw) }, { status: upstream.status });
    }
    return NextResponse.json(payload ?? { ok: true }, { status: upstream.status || 201 });
  } catch (error) {
    const detail = error instanceof Error ? error.message : "MausBot proxy unreachable";
    return NextResponse.json({ ok: false, detail }, { status: 503 });
  }
}
