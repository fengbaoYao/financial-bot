import { chromium } from "playwright";
import fs from "node:fs";
import path from "node:path";

const out = path.resolve("output/fb-product");
fs.mkdirSync(out, { recursive: true });
const base = process.env.FB_BASE || "http://127.0.0.1:3000";
const trustSession = process.env.FB_TRUST_SESSION || "96381545-8ced-4c22-8c59-7be83600e629";
const summary = {
  base,
  at: new Date().toISOString(),
  design: "100% copy Designing Grok Bot article UX structure (roster|chat|workspace)",
  shots: [],
  checks: {},
  diffFromPrevious: [],
};

async function shot(page, name) {
  const file = path.join(out, name);
  await page.screenshot({ path: file, fullPage: false });
  summary.shots.push(name);
  return file;
}

const browser = await chromium.launch({ headless: true });
const errors = [];
try {
  const context = await browser.newContext({
    viewport: { width: 1440, height: 900 },
    locale: "zh-CN",
  });
  const page = await context.newPage();
  page.on("pageerror", (err) => errors.push(String(err)));
  await page.addInitScript((sessionId) => {
    try {
      localStorage.setItem("financial-bot-language", "zh");
      localStorage.setItem("financial-bot-product-mode-v1", "use");
      localStorage.setItem("financial-bot-theme", "light");
      localStorage.setItem("financial-bot-session-v1", sessionId);
      localStorage.removeItem("financial-bot-focus-alert-demo-seeded-v1");
      localStorage.setItem(
        "financial-bot-focus-points-v1",
        JSON.stringify([
          {
            id: "focus-copy-demo",
            question: "盯着 US.AAPL 最新财报后指引有没有改口？",
            symbol: "US.AAPL",
            source: "local",
            createdAt: new Date().toISOString(),
            updatedAt: new Date().toISOString(),
          },
          {
            id: "focus-copy-demo-2",
            question: "腾讯近几期异动原因有没有新说法？",
            symbol: "00700.HK",
            source: "local",
            createdAt: new Date().toISOString(),
            updatedAt: new Date().toISOString(),
          },
        ]),
      );
      localStorage.removeItem("financial-bot-focus-alerts-v1");
      sessionStorage.removeItem("financial-bot-rail-cue-seen");
    } catch {}
  }, trustSession);

  await page.goto(`${base}/bot`, { waitUntil: "domcontentloaded", timeout: 60000 });
  await page.waitForTimeout(4200);

  const shell = page.locator("[data-copy-shell='true']").first();
  summary.checks.copyShell = await shell.count();
  summary.checks.workspaceLevel = await page.locator("[data-workspace-level]").first().getAttribute("data-workspace-level");
  summary.checks.panelOpen = await page.locator("[data-panel-open]").first().getAttribute("data-panel-open");
  summary.checks.rosterWatching = await page.locator("[data-roster-watching='true']").count();
  summary.checks.focusRosterItems = await page.locator('[class*="focusRosterItem"]').count();
  summary.checks.routines = await page.locator("[data-routines='true']").count();
  summary.checks.inlineCards = await page.locator('[class*="inlineActionCard"]').count();
  summary.checks.screenPreview = await page.locator('[class*="botScreenPreview"]').count();
  summary.checks.presenceAvatar = await page.locator('[class*="presenceAvatarWrap"]').count();
  summary.checks.oldUseWatchingVisible = await page.locator('[class*="useWatching"]:visible').count().catch(() => -1);
  summary.checks.pageErrors = errors.slice(0, 8);

  // Measure three columns roughly via bounding boxes
  const roster = page.locator('[class*="rosterRail"]').first();
  const stage = page.locator('[class*="stageColumn"]').first();
  const rail = page.locator('[class*="contextRail"]').first();
  const boxes = {};
  for (const [k, loc] of [["roster", roster], ["stage", stage], ["rail", rail]]) {
    if (await loc.count()) {
      const b = await loc.boundingBox();
      boxes[k] = b ? { x: Math.round(b.x), w: Math.round(b.width), visible: b.width > 40 } : null;
    }
  }
  summary.checks.columns = boxes;
  summary.diffFromPrevious = [
    "Previous redesign kept right rail closed in use mode (2-col). Now panelOpen defaults true → visible 3-col.",
    "Watching moved from top dashboard cards into left roster rows + compact inline chat cards.",
    "Right rail always shows screen preview + Routines list with Status/Preview/Full levels.",
    "Presence status sits on avatar (dot + hover detail), not only a top text chip.",
  ];

  await shot(page, "ux-copy-01-shell.png");

  // Crop emphasis: roster + chat — still full page but ensure roster items visible
  if (await roster.count()) await roster.scrollIntoViewIfNeeded().catch(() => {});
  await shot(page, "ux-copy-02-roster-chat.png");

  const card = page.locator('[class*="inlineActionCard"]').first();
  if (await card.count()) {
    await card.scrollIntoViewIfNeeded();
    await page.waitForTimeout(250);
  }
  await shot(page, "ux-copy-03-inline-card.png");

  if (await rail.count()) await rail.scrollIntoViewIfNeeded().catch(() => {});
  // Ensure preview level
  const previewBtn = page.getByRole("button", { name: "预览", exact: true });
  if (await previewBtn.count()) await previewBtn.click().catch(() => {});
  await page.waitForTimeout(400);
  await shot(page, "ux-copy-04-workspace.png");

  // Optional takeover check
  const fullBtn = page.getByRole("button", { name: "全宽", exact: true });
  if (await fullBtn.count()) {
    await fullBtn.click().catch(() => {});
    await page.waitForTimeout(500);
    summary.checks.takeoverLevel = await page.locator("[data-workspace-level]").first().getAttribute("data-workspace-level");
    await shot(page, "ux-copy-05-takeover.png");
    const back = page.getByRole("button", { name: "预览", exact: true });
    if (await back.count()) await back.click().catch(() => {});
  }

  fs.writeFileSync(path.join(out, "ux-copy-summary.json"), JSON.stringify(summary, null, 2));
  console.log(JSON.stringify(summary, null, 2));
} finally {
  await browser.close();
}
