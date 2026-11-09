import { NextRequest } from "next/server";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

const UPSTREAM = process.env.MAUSBOT_PROXY_URL || "http://127.0.0.1:18800";
const SAFE = /^[\w.-]+$/;

export async function GET(_req: NextRequest, ctx: { params: Promise<{ name: string }> | { name: string } }) {
  const resolved = await Promise.resolve(ctx.params);
  const name = String(resolved?.name || "");
  if (!SAFE.test(name)) {
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
    const ct = upstream.headers.get("content-type");
    if (ct) headers.set("Content-Type", ct);
    const cd = upstream.headers.get("content-disposition");
    if (cd) headers.set("Content-Disposition", cd);
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
