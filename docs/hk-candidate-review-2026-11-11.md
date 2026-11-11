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

The authenticated administrator success path and chat/attachment/voice/browser flows against a running Mausbot proxy were not exercised. No server was changed or deployed. Keep this candidate separate from `main` until those flows are verified.
