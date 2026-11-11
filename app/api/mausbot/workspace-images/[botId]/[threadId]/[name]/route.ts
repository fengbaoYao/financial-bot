import { NextRequest } from "next/server";
import { requireMausbotProxyAdmin } from "../../../../_auth";

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
  if (lower.endsWith(".pdf")) return "application/pdf";
  return null;
}

function decodeParam(value: string): string {
  let name = String(value || "");
  try {
    name = decodeURIComponent(name);
  } catch {
    /* already decoded */
  }
  return name;
}

export async function GET(
  _req: NextRequest,
  ctx: {
    params:
      Promise<{ botId: string; threadId: string; name: string }>;
  },
) {
  const denied = await requireMausbotProxyAdmin();
  if (denied) return denied;
  const resolved = await ctx.params;
  const botId = decodeParam(String(resolved?.botId || ""));
  const threadId = decodeParam(String(resolved?.threadId || ""));
  const name = decodeParam(String(resolved?.name || ""));
  if (
    !isSafeAttachmentName(botId) ||
    !isSafeAttachmentName(threadId) ||
    !isSafeAttachmentName(name) ||
    !imageTypeFor(name)
  ) {
    return new Response(JSON.stringify({ ok: false, detail: "invalid workspace image path" }), {
      status: 400,
      headers: { "Content-Type": "application/json" },
    });
  }

  try {
    const upstream = await fetch(
      `${UPSTREAM}/api/mausbot/workspace-images/${encodeURIComponent(botId)}/${encodeURIComponent(threadId)}/${encodeURIComponent(name)}`,
      {
        cache: "no-store",
        headers: { Accept: "image/*,application/pdf,*/*" },
        signal: AbortSignal.timeout(60_000),
      },
    );
    if (!upstream.ok || !upstream.body) {
      const raw = await upstream.text().catch(() => "");
      return new Response(JSON.stringify({ ok: false, detail: raw || `workspace image error (${upstream.status})` }), {
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
    headers.set("Content-Disposition", `inline; filename="${name}"`);
    headers.set("Cache-Control", "private, max-age=120");
    return new Response(upstream.body, { status: 200, headers });
  } catch (error) {
    const detail = error instanceof Error ? error.message : "MausBot proxy unreachable";
    return new Response(JSON.stringify({ ok: false, detail }), {
      status: 503,
      headers: { "Content-Type": "application/json" },
    });
  }
}
