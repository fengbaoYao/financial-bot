import { NextResponse } from "next/server";
import { getBrowserState, runBrowserAction } from "@/lib/mausbot-browser";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

export async function GET() {
  try {
    return NextResponse.json(await getBrowserState(), { headers: { "Cache-Control": "no-store" } });
  } catch {
    return NextResponse.json({ ok: false, detail: "浏览器状态暂不可用" }, { status: 503 });
  }
}

export async function POST(request: Request) {
  let body: unknown;
  try { body = await request.json(); }
  catch { return NextResponse.json({ ok: false, detail: "请求格式无效" }, { status: 400 }); }
  if (!body || typeof body !== "object" || Array.isArray(body)) {
    return NextResponse.json({ ok: false, detail: "请求格式无效" }, { status: 400 });
  }
  try {
    const result = await runBrowserAction(body as Parameters<typeof runBrowserAction>[0]);
    return NextResponse.json(result, { headers: { "Cache-Control": "no-store" } });
  } catch (error) {
    const detail = error instanceof Error ? error.message : "浏览器操作失败";
    const status = /尚未启动|无效|仅支持|不能|拦截|超过|超出|不支持|不能为空|标签页已关闭/.test(detail) ? 400 : 503;
    return NextResponse.json({ ok: false, detail }, { status });
  }
}
