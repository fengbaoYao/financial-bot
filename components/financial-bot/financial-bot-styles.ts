import base from "./financial-bot.module.css";

/**
 * Single CSS-module class map for the bot workbench.
 *
 * Everything (base layout, copy-shell chrome, additive UX) is concatenated into
 * `financial-bot.module.css` from `css-parts/` so every class name has exactly
 * one hash. Two modules used to be merged here with `{ ...base, ...ux }`, which
 * silently dropped the base hash for the 16 names both files used — that is how
 * the shell lost its 100dvh height chain and the transcript stopped scrolling.
 *
 * Edit the parts under `css-parts/` and run `npm run css:bundle`.
 */
export default base;
