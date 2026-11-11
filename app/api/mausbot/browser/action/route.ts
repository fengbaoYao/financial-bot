import { NextRequest } from "next/server";
import { requireMausbotProxyAdmin } from "../../_auth";
const UPSTREAM = (process.env.MAUSBOT_PROXY_URL || "http://127.0.0.1:18800").replace(/\/$/, "");
export const runtime = "nodejs";
export const dynamic = "force-dynamic";
export async function POST(req: NextRequest) {
  const denied = await requireMausbotProxyAdmin();
  if (denied) return denied;
  const body = await req.text();
  const res = await fetch(`${UPSTREAM}/api/mausbot/browser/action`, { method: "POST", headers: { "content-type": "application/json" }, body, cache: "no-store" });
  const text = await res.text();
  return new Response(text, { status: res.status, headers: { "content-type": "application/json" } });
}
