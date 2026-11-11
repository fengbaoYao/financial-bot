import { NextResponse } from "next/server";
import { proxyJson } from "../_upstream";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

export async function GET() {
  try {
    const { upstream, payload } = await proxyJson("/api/mausbot/health", { timeoutMs: 8_000 });
    return NextResponse.json(payload, { status: upstream.status });
  } catch {
    return NextResponse.json({ ok: false, error: "MausBot proxy unreachable" }, { status: 503 });
  }
}
