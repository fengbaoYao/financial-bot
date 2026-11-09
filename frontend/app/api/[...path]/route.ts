import { NextResponse } from "next/server";

const BACKEND =
  process.env.BACKEND_BASE_URL ??
  process.env.NEXT_PUBLIC_API_BASE_URL ??
  "http://127.0.0.1:8000";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

function isPublicCacheableGet(path: string[], searchParams: URLSearchParams) {
  if (path.length === 2 && path[0] === "stocks" && path[1] === "search") {
    return false;
  }
  if (path.length === 3 && path[0] === "stocks" && path[2] === "analysis") {
    return searchParams.get("include_ai") === "false";
  }
  if (path.length === 3 && path[0] === "stocks" && path[2] === "news") {
    return true;
  }
  if (path.length === 1 && (path[0] === "hotspots" || path[0] === "market-regime")) {
    return true;
  }
  if (path.length === 2 && path[0] === "news" && path[1] === "global") {
    return true;
  }
  if (path.length === 2 && path[0] === "web" && path[1] === "search") {
    return true;
  }
  if (path.length === 2 && path[0] === "hotspots") {
    return true;
  }
  if (path.length === 3 && path[0] === "strategies" && path[1] === "can-slim" && path[2] === "screen") {
    return true;
  }
  return false;
}

async function proxyRequest(
  request: Request,
  context: { params: Promise<{ path: string[] }> },
) {
  const { path } = await context.params;
  const backendPath = path.map((segment) => encodeURIComponent(segment)).join("/");
  const url = new URL(request.url);
  const backendUrl = new URL(`${BACKEND.replace(/\/$/, "")}/api/${backendPath}`);
  backendUrl.search = url.search;
  const isCacheableGet = request.method === "GET" && isPublicCacheableGet(path, url.searchParams);

  const headers = new Headers(request.headers);
  headers.delete("host");
  headers.delete("content-length");
  // Keep backend request.client as the Next loopback peer. Uvicorn trusts
  // X-Forwarded-* by default; Cloudflare would otherwise rewrite client IP
  // and trip openmuse_shared_requires_local_request.
  for (const name of [
    "x-forwarded-for",
    "x-forwarded-host",
    "x-forwarded-proto",
    "x-forwarded-port",
    "x-real-ip",
    "forwarded",
    "cf-connecting-ip",
  ]) {
    headers.delete(name);
  }

  const init: RequestInit = {
    method: request.method,
    headers,
    cache: isCacheableGet ? "default" : "no-store",
  };

  if (request.method !== "GET" && request.method !== "HEAD") {
    init.body = await request.arrayBuffer();
  }

  // Bound hung CN/Yahoo provider calls so the research panel can show retry UI.
  const needsDeadline =
    path[0] === "stocks" &&
    (path[2] === "analysis" || path[2] === "news" || path[1] === "search");
  const deadlineMs = path[2] === "analysis" ? 20000 : path[2] === "news" ? 12000 : 8000;
  let timeoutId: ReturnType<typeof setTimeout> | undefined;
  if (needsDeadline) {
    const controller = new AbortController();
    timeoutId = setTimeout(() => controller.abort(), deadlineMs);
    init.signal = controller.signal;
  }

  try {
    const res = await fetch(backendUrl, init);
    const responseHeaders = new Headers(res.headers);
    if (!isCacheableGet) {
      responseHeaders.set("Cache-Control", "no-store");
    }
    return new Response(res.body, {
      status: res.status,
      headers: responseHeaders,
    });
  } catch (error) {
    const aborted = error instanceof Error && error.name === "AbortError";
    const message = aborted
      ? `Upstream timed out after ${Math.round(deadlineMs / 1000)}s`
      : error instanceof Error
        ? error.message
        : "Proxy error";
    return NextResponse.json(
      { detail: message },
      { status: aborted ? 504 : 502, headers: { "Cache-Control": "no-store" } },
    );
  } finally {
    if (timeoutId) clearTimeout(timeoutId);
  }
}

export async function GET(request: Request, context: { params: Promise<{ path: string[] }> }) {
  return proxyRequest(request, context);
}

export async function POST(request: Request, context: { params: Promise<{ path: string[] }> }) {
  return proxyRequest(request, context);
}

export async function PUT(request: Request, context: { params: Promise<{ path: string[] }> }) {
  return proxyRequest(request, context);
}

export async function PATCH(request: Request, context: { params: Promise<{ path: string[] }> }) {
  return proxyRequest(request, context);
}

export async function DELETE(request: Request, context: { params: Promise<{ path: string[] }> }) {
  return proxyRequest(request, context);
}

export async function OPTIONS() {
  return NextResponse.json({}, { status: 204 });
}
