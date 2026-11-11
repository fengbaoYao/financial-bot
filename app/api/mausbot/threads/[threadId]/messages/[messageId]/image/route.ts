import { NextRequest, NextResponse } from "next/server";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

const PROXY =
  process.env.MAUSBOT_PROXY_URL?.replace(/\/$/, "") ||
  "http://127.0.0.1:18800";

export async function GET(
  _req: NextRequest,
  ctx: { params: Promise<{ threadId: string; messageId: string }> | { threadId: string; messageId: string } },
) {
  const params = await Promise.resolve(ctx.params);
  const threadId = encodeURIComponent(params.threadId);
  const messageId = encodeURIComponent(params.messageId);
  const url = `${PROXY}/api/mausbot/threads/${threadId}/messages/${messageId}/image`;
  try {
    const res = await fetch(url, {
      method: "GET",
      headers: { Accept: "*/*" },
      cache: "no-store",
    });
    const buf = await res.arrayBuffer();
    const ct = res.headers.get("content-type") || "image/png";
    return new NextResponse(buf, {
      status: res.status,
      headers: {
        "Content-Type": ct,
        "Cache-Control": "private, max-age=120",
      },
    });
  } catch (e) {
    return NextResponse.json(
      { error: e instanceof Error ? e.message : String(e) },
      { status: 502 },
    );
  }
}
