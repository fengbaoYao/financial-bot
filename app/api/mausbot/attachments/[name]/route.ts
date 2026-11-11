import { NextRequest } from "next/server";
import { requireMausbotProxyAdmin } from "../../_auth";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

const UPSTREAM = process.env.MAUSBOT_PROXY_URL || "http://127.0.0.1:18800";

function isSafeAttachmentName(name: string): boolean {
  if (!name || name.length > 180) return false;
  if (name === "." || name === ".." || name.includes("..")) return false;
  if (/[\\/\0\s<>:"|?*]/.test(name)) return false;
  return true;
}

function imageTypeFor(name: string): string | null {
  const lower = name.toLowerCase();
  if (lower.endsWith(".png")) return "image/png";
  if (lower.endsWith(".jpg") || lower.endsWith(".jpeg")) return "image/jpeg";
  if (lower.endsWith(".gif")) return "image/gif";
  if (lower.endsWith(".webp")) return "image/webp";
  if (lower.endsWith(".svg")) return "image/svg+xml";
  return null;
}

export async function GET(_req: NextRequest, ctx: { params: Promise<{ name: string }> }) {
  const denied = await requireMausbotProxyAdmin();
  if (denied) return denied;
  const resolved = await ctx.params;
  let name = String(resolved?.name || "");
  try {
    name = decodeURIComponent(name);
  } catch {
    /* already decoded */
  }
  if (!isSafeAttachmentName(name)) {
    return new Response(JSON.stringify({ ok: false, detail: "invalid attachment name" }), {
      status: 400,
      headers: { "Content-Type": "application/json" },
    });
  }

  try {
    const upstream = await fetch(`${UPSTREAM}/api/mausbot/attachments/${encodeURIComponent(name)}`, {
      cache: "no-store",
      headers: { Accept: "*/*" },
      signal: AbortSignal.timeout(60_000),
    });
    if (!upstream.ok || !upstream.body) {
      const raw = await upstream.text().catch(() => "");
      return new Response(JSON.stringify({ ok: false, detail: raw || `attachment error (${upstream.status})` }), {
        status: upstream.status || 502,
        headers: { "Content-Type": "application/json" },
      });
    }
    const headers = new Headers();
    const upstreamType = upstream.headers.get("content-type") || "";
    const sniffed = imageTypeFor(name);
    const type = !upstreamType || upstreamType.startsWith("application/octet-stream")
      ? (sniffed || upstreamType || "application/octet-stream")
      : upstreamType;
    headers.set("Content-Type", type);
    if (type.startsWith("image/")) headers.set("Content-Disposition", "inline");
    else {
      const cd = upstream.headers.get("content-disposition");
      if (cd) headers.set("Content-Disposition", cd);
    }
    headers.set("Cache-Control", "private, max-age=300");
    return new Response(upstream.body, { status: 200, headers });
  } catch (error) {
    const detail = error instanceof Error ? error.message : "MausBot proxy unreachable";
    return new Response(JSON.stringify({ ok: false, detail }), {
      status: 503,
      headers: { "Content-Type": "application/json" },
    });
  }
}
