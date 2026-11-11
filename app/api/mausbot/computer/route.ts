import { NextResponse } from "next/server";
import { proxyJson } from "../_upstream";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

export async function GET() {
  try {
    const { upstream, payload, raw } = await proxyJson("/api/mausbot/computer", {
      method: "GET",
      timeoutMs: 15000,
    });
    if (!upstream.ok) {
      const detail =
        payload && typeof payload === "object" && "detail" in payload && typeof (payload as { detail?: unknown }).detail === "string"
          ? (payload as { detail: string }).detail
          : raw || "upstream error";
      return NextResponse.json({ ok: false, detail }, { status: upstream.status });
    }
    return NextResponse.json(payload ?? { ok: true }, { status: 200 });
  } catch (error) {
    const detail = error instanceof Error ? error.message : "MausBot proxy unreachable";
    return NextResponse.json({ ok: false, detail }, { status: 503 });
  }
}
