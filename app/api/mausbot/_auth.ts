import { NextResponse } from "next/server";

import { createClient } from "@/utils/supabase/server";

const LOCAL_ACCESS_OPT_IN = "MAUSBOT_PROXY_ALLOW_UNAUTHENTICATED_LOCAL";

/**
 * Browser control and file uploads are server-side capabilities. Require a
 * verified Supabase administrator in production. Local development without
 * Supabase remains available outside production; production local mode must be
 * explicitly enabled for a trusted private instance.
 */
export async function requireMausbotProxyAdmin(): Promise<NextResponse | null> {
  const supabaseConfigured = Boolean(
    process.env.NEXT_PUBLIC_SUPABASE_URL &&
      process.env.NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY,
  );

  if (!supabaseConfigured) {
    const explicitlyAllowed = process.env[LOCAL_ACCESS_OPT_IN] === "1";
    if (process.env.NODE_ENV !== "production" || explicitlyAllowed) return null;
    return NextResponse.json(
      { detail: "Mausbot proxy access requires Supabase authentication." },
      { status: 503 },
    );
  }

  try {
    const supabase = await createClient();
    const { data: userData, error: userError } = await supabase.auth.getUser();
    if (userError || !userData.user) {
      return NextResponse.json({ detail: "Sign in required." }, { status: 401 });
    }

    const { data: adminRecord, error: adminError } = await supabase
      .from("admin_users")
      .select("user_id")
      .eq("user_id", userData.user.id)
      .maybeSingle();

    if (adminError) {
      return NextResponse.json(
        { detail: "Could not verify Mausbot proxy access." },
        { status: 503 },
      );
    }
    if (!adminRecord) {
      return NextResponse.json({ detail: "Administrator access required." }, { status: 403 });
    }
    return null;
  } catch {
    return NextResponse.json(
      { detail: "Mausbot proxy access could not be verified." },
      { status: 503 },
    );
  }
}
