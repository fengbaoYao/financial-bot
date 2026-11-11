import { NextRequest } from "next/server";
import { requireMausbotProxyAdmin } from "../_auth";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

const UPSTREAM = (process.env.MAUSBOT_PROXY_URL || "http://127.0.0.1:18800").replace(/\/$/, "");

export async function POST(req: NextRequest) {
  const denied = await requireMausbotProxyAdmin();
  if (denied) return denied;
  const search = new URL(req.url).search;
  const body = await req.arrayBuffer();
  const res = await fetch(`${UPSTREAM}/api/mausbot/attachments${search}`, {
    method: "POST",
    headers: {
      "content-type": req.headers.get("content-type") || "application/octet-stream",
    },
    body,
    cache: "no-store",
  });
  const text = await res.text();
  return new Response(text, {
    status: res.status,
    headers: { "content-type": res.headers.get("content-type") || "application/json" },
  });
}
