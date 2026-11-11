import { requireMausbotProxyAdmin } from "../../_auth";
const UPSTREAM = (process.env.MAUSBOT_PROXY_URL || "http://127.0.0.1:18800").replace(/\/$/, "");
export const runtime = "nodejs";
export const dynamic = "force-dynamic";
export async function GET() {
  const denied = await requireMausbotProxyAdmin();
  if (denied) return denied;
  const res = await fetch(`${UPSTREAM}/api/mausbot/browser/screenshot`, { cache: "no-store" });
  if (!res.ok) {
    const text = await res.text();
    return new Response(text, { status: res.status, headers: { "content-type": "application/json" } });
  }
  const buf = await res.arrayBuffer();
  return new Response(buf, { status: 200, headers: { "content-type": "image/png", "cache-control": "no-store" } });
}
