# Hong Kong candidate review

Date: 2026-11-11

## Git state

- `main` now points to the Silicon Valley baseline: `5e636fe`.
- The former demo `main` commit `359b767` is preserved as `backup/demo-main-20261111`.
- Hong Kong candidate before this review note: `232cb94`, based on the Silicon Valley baseline.
- The original `codex/financial-bot-mvp` checkout and its existing working changes were left in place.

## Fixes in the candidate

- Restored the Silicon Valley research page at `/work`.
- Removed the undefined `session_id` use from notification reads.
- Restored a mocked Eastmoney fallback failure in the local research test.
- Added a server-side Supabase administrator check for browser control, browser state/screenshots, browser installation, file uploads and related attachment/image reads. Production denies access when Supabase is missing; local development remains available. A private production local instance needs the explicit `MAUSBOT_PROXY_ALLOW_UNAUTHENTICATED_LOCAL=1` setting.
- Updated three dynamic route handlers to use Next.js 16's promised `params` type so production compilation succeeds.

## Validation

- Backend notification and deterministic fallback tests: 2 passed.
- Frontend typecheck: passed.
- ESLint on changed routes, `proxy.ts` and `/work`: passed.
- CSS module check: passed.
- Next.js production Webpack build: passed.
- Runtime check: `/work` returned HTTP 200 with research content; production browser-state access without Supabase returned HTTP 503 before reaching the proxy.
- `git diff --check`: passed.

At the time of this review, the authenticated administrator success path and flows against a running Mausbot proxy had not been exercised. The follow-up below records the later Hong Kong deployment and verification.

## Follow-up: Hong Kong password login deployment

Date: 2026-11-11

- Added a standalone administrator password login, signed HTTP-only session cookie (12-hour expiry), and browser-panel login form. No Supabase project is required. The password and separate random signing key are stored only in the server environment; their values are not recorded here.
- Deployed the candidate frontend to `/opt/financial-bot/frontend` on the Hong Kong ECS (`aliyun-ecs`, Tailscale IP `100.112.205.104`). The backend service was not changed. The existing web-entry marker now points to `https://aliyun-ecs.taild33970.ts.net`.
- Built on the server with Node 22.23.4; the Next.js production build and TypeScript compilation passed. `financial-bot-web.service` is active.
- Public checks: `/bot` returned 200; unauthenticated browser state, screenshot and file-upload calls returned 401; a wrong password returned 401; cross-origin login returned 403; the configured password logged in with 200; authenticated browser state and screenshot returned 200 (`image/png`, 1280×577).
- Browser action check: created and closed one empty tab; the original one-tab state and active URL were restored.
- Rollback copies are on the server at `/opt/financial-bot/rollback-mausbot-admin-20261111-1215`. No Git push was made. The root `deploy.json` was left unchanged because this deployment updated only the frontend, not the backend.
- The browser inspection reported one unrelated-looking `GET /api/brand` 404; the candidate source contains no `/api/brand` reference. The `/bot` page itself loaded and connected.

## Follow-up: public view without password login

Date: 2026-11-11

- Removed the standalone password login form and route from the Hong Kong frontend. The public `/bot` view loads without asking visitors for a password. Browser state and control remain restricted to a verified Supabase administrator; with Supabase not configured, the browser panel reports unavailable (HTTP 503).
- Deployed the updated frontend only. The FastAPI backend was not changed. A rollback snapshot is at `/opt/financial-bot/rollback-mausbot-public-display-20261111/runtime`; source-file copies are under the same rollback directory. No Git push or branch merge was made.
- Hong Kong production build passed. The service is active and the public `/bot` page renders. The first local HTTP probe ran before the restarted service began listening; a fresh built-in-browser check then loaded the public page successfully.
- `/api/brand` responds with FastAPI's generic `{"detail":"Not Found"}`. No route or caller exists in the checked frontend or backend source. A fresh `/bot` load did not request it in the page's Resource Timing entries; the original initiator therefore remains unidentified. Do not add a guessed endpoint until the caller is found.
- The candidate remains two commits ahead of local `main` (`5e636fe` base, `232cb94` implementation, `3e6d707` review record). The live server does not contain Git metadata, so its deployment cannot be tied to an exact commit by `deploy.json` alone.

## Follow-up: public proxy access and desktop viewer brand request

Date: 2026-11-11

- The owner explicitly requested open public access. Removed the Supabase administrator gate from the MausBot browser, screenshot, browser-engine and file proxy routes in the candidate. `npm run build` passed. This candidate change has not been deployed yet: a later SSH connection to the Hong Kong host was closed before the service configuration could be changed.
- Located the `/api/brand` caller in the installed OpenMausBot browser bundle, `/usr/local/lib/nodejs/node-v22.23.4-linux-x64/lib/node_modules/openmausbot/dist/assets/styles-D3cGFjx0.js`. It was an optional host-brand fetch that already fell back to its built-in default on error. Replaced that fetch helper with the default-brand return and preserved the original bundle as `styles-D3cGFjx0.js.bak-20261111-brand-fix`.
- A fresh no-cache read of both served bundle URLs no longer contains `/api/brand`; the desktop viewer rendered and showed `Desktop connected`. Browser diagnostics continued to include an earlier 404 report, while the current page's Resource Timing list contained no `/api/brand` entry, so a clean-context console result is not confirmed.
- This is a patch to the installed built asset because the OpenMausBot app source is not present in this candidate. An OpenMausBot package update may replace it; the durable upstream source change is still needed.
