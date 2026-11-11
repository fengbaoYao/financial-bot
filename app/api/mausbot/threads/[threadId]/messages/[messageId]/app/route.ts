import { NextRequest, NextResponse } from "next/server";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

const UPSTREAM = process.env.MAUSBOT_PROXY_URL || "http://127.0.0.1:18800";

export async function GET(req: NextRequest, ctx: { params: Promise<{ threadId: string; messageId: string }> }) {
  const { threadId, messageId } = await ctx.params;
  const rev = req.nextUrl.searchParams.get("rev");
  const qs = rev && /^\d+$/.test(rev) ? `?rev=${rev}` : "";
  try {
    const upstream = await fetch(
      `${UPSTREAM}/api/mausbot/threads/${encodeURIComponent(threadId)}/messages/${encodeURIComponent(messageId)}/app${qs}`,
      { cache: "no-store", signal: AbortSignal.timeout(30000) },
    );
    const buf = await upstream.arrayBuffer();
    if (!upstream.ok) {
      let detail = `upstream ${upstream.status}`;
      try {
        const j = JSON.parse(new TextDecoder().decode(buf));
        detail = String(j.detail || j.error || detail);
      } catch { /* keep */ }
      return NextResponse.json({ ok: false, detail }, { status: upstream.status });
    }
    const ctype = upstream.headers.get("content-type") || "text/html; charset=utf-8";
    return new NextResponse(buf, {
      status: 200,
      headers: { "Content-Type": ctype, "Cache-Control": "no-store" },
    });
  } catch (error) {
    const detail = error instanceof Error ? error.message : "MausBot proxy unreachable";
    return NextResponse.json({ ok: false, detail }, { status: 503 });
  }
}
