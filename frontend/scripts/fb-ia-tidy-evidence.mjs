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
  design: "IA tidy — use path vs watching vs adjust",
  shots: [],
  checks: {},
};

async function shot(page, name) {
  const file = path.join(out, name);
  await page.screenshot({ path: file, fullPage: false });
  summary.shots.push(name);
  return file;
}

const browser = await chromium.launch({ headless: true });
try {
  const context = await browser.newContext({
    viewport: { width: 1440, height: 900 },
    locale: "zh-CN",
  });
  const page = await context.newPage();
  await page.addInitScript((sessionId) => {
    try {
      localStorage.setItem("financial-bot-language", "zh");
      localStorage.setItem("financial-bot-product-mode-v1", "use");
      localStorage.setItem("financial-bot-session-v1", sessionId);
      localStorage.removeItem("financial-bot-focus-alert-demo-seeded-v1");
      localStorage.setItem(
        "financial-bot-focus-points-v1",
        JSON.stringify([
          {
            id: "focus-ia-demo",
            question: "盯着 US.AAPL 最新财报后指引有没有改口？",
            symbol: "US.AAPL",
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
  await page.waitForTimeout(3500);

  const shell = page.locator("[data-product-mode]").first();
  summary.checks.defaultMode = await shell.getAttribute("data-product-mode").catch(() => null);

  // Adjust entry should be single (stage header), not roster brand
  summary.checks.adjustExactCount = await page.getByRole("button", { name: "调整能力", exact: true }).count();
  summary.checks.rosterBrandAdjust = await page.locator('[class*="rosterBuildHint"]').count();
  summary.checks.opsMoreToggle = await page.locator("[data-ops-more='true']").count();
  summary.checks.opsPanelsClosed = await page.locator('[data-ops-panels="closed"]').count();
  summary.checks.sideTabMonitorVisible = await page.getByRole("tab", { name: "监控" }).isVisible().catch(() => false);
  summary.checks.workspaceTitle = (await page.locator('[class*="workspaceUseTitle"]').first().innerText().catch(() => "")).trim();
  summary.checks.watchingRoster = await page.locator("[data-roster-watching='true']").count();
  summary.checks.inlineWatchingCards = await page.locator('[data-use-watching="true"] [data-kind="watching"]').count();
  summary.checks.inlineUnreadCards = await page.locator("[data-watching-alert]").count();
  summary.checks.welcomeMore = await page.locator('[class*="welcomeMoreTemplates"]').count();
  summary.checks.successPaths = await page.locator('[class*="successPathCard"]').count();
  summary.checks.levelSwitchVisible = await page.locator('[class*="workspaceLevelSwitch"]').isVisible().catch(() => false);

  await shot(page, "ia-01-use-home.png");

  // Open unread / watching from left roster
  const focusItem = page.locator("[data-roster-watching='true'] button").first();
  if (await focusItem.count()) {
    await focusItem.click();
    await page.waitForTimeout(1200);
  }
  summary.checks.afterFocusClickMode = await shell.getAttribute("data-product-mode").catch(() => null);
  await shot(page, "ia-02-watching-unread.png");

  // Right rail routines / screen — ensure visible
  const routines = page.locator("[data-routines='true']");
  summary.checks.routinesVisible = await routines.isVisible().catch(() => false);
  await shot(page, "ia-03-screen-routines.png");

  // Expand 更多
  const more = page.locator("[data-ops-more='true']").first();
  if (await more.count()) {
    await more.click();
    await page.waitForTimeout(600);
  }
  summary.checks.opsPanelsOpen = await page.locator('[data-ops-panels="open"]').count();
  summary.checks.sideTabMonitorVisibleAfter = await page.getByRole("tab", { name: "监控" }).isVisible().catch(() => false);
  await shot(page, "ia-04-ops-more.png");

  // Adjust capabilities entry
  const adjust = page.getByRole("button", { name: "调整能力", exact: true }).first();
  if (await adjust.count()) {
    await adjust.click();
    await page.waitForTimeout(1200);
  }
  summary.checks.afterAdjustMode = await shell.getAttribute("data-product-mode").catch(() => null);
  summary.checks.levelSwitchInBuild = await page.locator('[class*="workspaceLevelSwitch"]').isVisible().catch(() => false);
  await shot(page, "ia-05-adjust-build.png");

  fs.writeFileSync(path.join(out, "feat-ia-summary.json"), JSON.stringify(summary, null, 2));
  console.log(JSON.stringify(summary, null, 2));
} finally {
  await browser.close();
}
