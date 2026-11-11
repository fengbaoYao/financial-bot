# Mausbot proxy access

The browser state, screenshot, browser control, browser-engine install and file upload routes proxy to capabilities on the application host. In production they require a verified Supabase user who is present in `admin_users`. Missing Supabase configuration, an unavailable auth service, or an admin lookup failure denies access.

For local development without Supabase, these routes remain available when `NODE_ENV` is not `production`. A trusted private production instance without Supabase may opt in with `MAUSBOT_PROXY_ALLOW_UNAUTHENTICATED_LOCAL=1`. Do not set this option on a public or shared host.
