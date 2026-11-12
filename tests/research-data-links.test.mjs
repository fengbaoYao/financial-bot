import test from "node:test";
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { resolve } from "node:path";

const source = await readFile(resolve("components/research-trace.tsx"), "utf8");

test("announcement rows without an original URL cannot become blank-page links", () => {
  assert.equal(source.includes('href={String(row.url || "#")}'), false);
  assert.match(source, /sourceUrl\s*=\s*isExternalResearchLink\(row\.url\)\s*\?\s*row\.url\s*:\s*null/);
  assert.match(source, /hasSourceUrl\s*\?\s*<a\b/);
  assert.match(source, /原文链接暂不可用/);
  assert.match(source, /原文链接暂不可用.*small/);
});

test("announcement links only allow absolute HTTP(S) source URLs", () => {
  assert.match(source, /isExternalResearchLink\(row\.url\)/);
  assert.match(source, /new URL\(value\)/);
  assert.match(source, /url\.protocol === "https:" \|\| url\.protocol === "http:"/);
});
