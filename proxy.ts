import { type NextRequest, NextResponse } from "next/server";

import { updateSession } from "@/utils/supabase/middleware";

export async function proxy(request: NextRequest) {
  const path = request.nextUrl.pathname;

  // Invalid Next-Action POSTs return a non-HTML 404. Safari treats a document
  // navigation to that response as "This page could not load".
  if (request.method === "POST") {
    const actionId = request.headers.get("next-action");
    if (actionId && actionId.length !== 42) {
      const dest = request.headers.get("sec-fetch-dest");
      const mode = request.headers.get("sec-fetch-mode");
      if (dest === "document" || mode === "navigate") {
        return NextResponse.redirect(request.nextUrl, 303);
      }
    }
  }

  // financial-bot shell and its BFF do not use Supabase auth.
  if (path === "/bot" || path.startsWith("/bot/") || path.startsWith("/api/mausbot")) {
    return NextResponse.next();
  }

  if (path === "/" || path === "/landing" || path.startsWith("/journal/")) {
    const requested = request.nextUrl.searchParams.get("lang");
    const language = requested === "en" || requested === "zh"
      ? requested : request.cookies.get("financial-bot-language")?.value === "zh" ? "zh" : "en";
    if (path === "/") {
      // Redirect before rendering/streaming so crawlers receive an HTTP redirect.
      // Use the incoming origin to keep local and staging navigation on that site.
      const destination = new URL("/landing", request.url);
      destination.searchParams.set("lang", language);
      return NextResponse.redirect(destination);
    }
    const requestHeaders = new Headers(request.headers);
    requestHeaders.set("x-financial-bot-page-language", language);
    // Public pages need no auth refresh; language must also be correct before hydration.
    return NextResponse.next({ request: { headers: requestHeaders } });
  }
  return updateSession(request);
}

export const config = {
  matcher: [
    "/((?!_next/static|_next/image|favicon.ico|robots.txt|sitemap.xml|opengraph-image|.*\\.(?:svg|png|jpg|jpeg|gif|webp|ico|mp4|webm|mov|woff|woff2|ttf)$).*)",
  ],
};
