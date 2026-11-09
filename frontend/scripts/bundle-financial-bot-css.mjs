#!/usr/bin/env node
/**
 * Concatenate components/financial-bot/css-parts/*.css into
 * financial-bot.module.css so CSS-module hashes stay unified.
 * Edit the parts, then: node scripts/bundle-financial-bot-css.mjs
 */
import { readdirSync, readFileSync, writeFileSync } from "node:fs";
import { join, dirname } from "node:path";
import { fileURLToPath } from "node:url";

const root = join(dirname(fileURLToPath(import.meta.url)), "..");
const partsDir = join(root, "components/financial-bot/css-parts");
const out = join(root, "components/financial-bot/financial-bot.module.css");
const files = readdirSync(partsDir).filter((f) => f.endsWith(".css")).sort();
const banner = "/* AUTO-BUNDLED from css-parts/ — run: node scripts/bundle-financial-bot-css.mjs */\n";
const body = files.map((f) => readFileSync(join(partsDir, f), "utf8").replace(/\s+$/, "")).join("\n\n");
writeFileSync(out, banner + body + "\n");
console.log(`bundled ${files.length} parts -> financial-bot.module.css (${body.length} chars)`);
