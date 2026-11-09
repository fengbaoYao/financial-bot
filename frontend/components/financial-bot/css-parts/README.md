# financial-bot CSS parts

Source fragments for `financial-bot.module.css`.

Next.js CSS Modules hash classes **per file**. Compound selectors like
`.shell[data-theme="light"] .studio*` must live in the **same** module as
`.shell` / `.studio*`, so these parts are concatenated into one
`financial-bot.module.css` before the bundler sees them.

## Edit workflow

1. Change the relevant `0N-*.css` file here.
2. Run `npm run css:bundle` (or `node scripts/bundle-financial-bot-css.mjs`).
3. Commit both the part **and** the regenerated `financial-bot.module.css`.

Additive-only new class names (no shared compounds) can go in
`../financial-bot-ux.module.css` instead — no bundle step required.
