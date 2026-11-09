export const runtime = "nodejs";
export const dynamic = "force-dynamic";

export const UPSTREAM = process.env.MAUSBOT_PROXY_URL || "http://127.0.0.1:18800";

export async function proxyJson(path: string, init?: RequestInit & { timeoutMs?: number }) {
  const timeoutMs = init?.timeoutMs ?? 30_000;
  const rest: RequestInit = { ...(init || {}) };
  delete (rest as { timeoutMs?: number }).timeoutMs;
  const upstream = await fetch(`${UPSTREAM}${path}`, {
    ...rest,
    cache: "no-store",
    signal: AbortSignal.timeout(timeoutMs),
  });
  const raw = await upstream.text();
  let payload: unknown = null;
  try {
    payload = raw ? JSON.parse(raw) : null;
  } catch {
    payload = { raw: raw.slice(0, 500) };
  }
  return { upstream, raw, payload };
}
