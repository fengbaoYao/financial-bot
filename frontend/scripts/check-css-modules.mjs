#!/usr/bin/env node
/**
 * Guards the two CSS-module traps that have already shipped silent bugs:
 *
 * 1. A **global** class name (written as a plain string in JSX, e.g.
 *    `className="markdown-body"`) referenced from a CSS module without
 *    `:global()`. CSS Modules hashes it, so the rule compiles to a class that
 *    never exists in the DOM and the styling silently never applies.
 * CSS Modules also hash class names per file, so every bot style now lives in
 * `css-parts/` and is bundled into one module: a name has exactly one hash.
 *
 * Usage: node scripts/check-css-modules.mjs   (exit 1 on violations)
 */
import { readdirSync, readFileSync, statSync } from "node:fs";
import { join, dirname, relative } from "node:path";
import { fileURLToPath } from "node:url";

const root = join(dirname(fileURLToPath(import.meta.url)), "..");
const problems = [];

function walk(dir, out = []) {
  for (const entry of readdirSync(dir)) {
    if (entry === "node_modules" || entry.startsWith(".")) continue;
    const full = join(dir, entry);
    if (statSync(full).isDirectory()) walk(full, out);
    else if (/\.(tsx|jsx)$/.test(entry)) out.push(full);
  }
  return out;
}

// 1) a class name is "global" when JSX writes it as a string literal and no
//    component ever reads it from the module class map (`styles.<name>`).
const literalClasses = new Set();
const moduleAccesses = new Set();
for (const file of [...walk(join(root, "components")), ...walk(join(root, "app"))]) {
  const source = readFileSync(file, "utf8");
  for (const match of source.matchAll(/className=(?:"([^"]+)"|\{`([^`]+)`\})/g)) {
    const raw = match[1] ?? match[2] ?? "";
    for (const token of raw.split(/[\s${}?:()|]+/)) {
      const name = token.trim();
      if (/^[a-z][a-z0-9-]{2,}$/.test(name)) literalClasses.add(name);
    }
  }
  for (const match of source.matchAll(/styles\.([A-Za-z_][\w]*)|styles\[`(?:\.|\$\{)?([A-Za-z_][\w]*)/g)) {
    moduleAccesses.add(match[1] ?? match[2]);
  }
}
const globalClasses = new Set([...literalClasses].filter((name) => !moduleAccesses.has(name)));

const cssFiles = readdirSync(join(root, "components/financial-bot/css-parts"))
  .filter((name) => name.endsWith(".css"))
  .map((name) => join(root, "components/financial-bot/css-parts", name));

function stripGlobals(css) {
  return css
    .replace(/\/\*[\s\S]*?\*\//g, " ") // comments mention class names; they are not selectors
    .replace(/:global\([^)]*\)/g, " ");
}

// 2) global class referenced from a module selector
for (const file of cssFiles) {
  const css = stripGlobals(readFileSync(file, "utf8"));
  const selectorSource = css.replace(/\{[^}]*\}/g, " ");
  const seen = new Set();
  for (const match of selectorSource.matchAll(/\.([a-zA-Z][\w-]*)/g)) {
    const name = match[1];
    if (!globalClasses.has(name) || seen.has(name)) continue;
    seen.add(name);
    problems.push(`${relative(root, file)}: ".${name}" is a global class used in JSX — write :global(.${name}) in the module`);
  }
}


if (problems.length) {
  console.error(`CSS module check failed (${problems.length}):`);
  for (const problem of problems) console.error(` - ${problem}`);
  process.exit(1);
}
console.log(`CSS module check passed (${cssFiles.length} parts, ${globalClasses.size} global classes).`);
