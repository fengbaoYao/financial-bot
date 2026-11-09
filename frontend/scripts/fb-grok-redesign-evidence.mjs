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
  design: "persistent-agent / Grok Bot article principles",
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
      // Fresh focus + allow demo unread sample
      localStorage.removeItem("financial-bot-focus-alert-demo-seeded-v1");
      localStorage.setItem(
        "financial-bot-focus-points-v1",
        JSON.stringify([
          {
            id: "focus-redesign-demo",
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

  // —— Home (use mode): conversation + watching status
  await page.goto(`${base}/bot`, { waitUntil: "domcontentloaded", timeout: 60000 });
  await page.waitForTimeout(3200);

  const shell = page.locator("[data-product-mode]").first();
  summary.checks.defaultMode = await shell.getAttribute("data-product-mode").catch(() => null);
  summary.checks.presenceText = (
    await page.locator('[class*="presenceLine"]').first().innerText().catch(() => "")
  ).replace(/\s+/g, " ").trim();
  summary.checks.adjustEntry = await page.getByRole("button", { name: "调整能力", exact: true }).count();
  summary.checks.modeToggleUseBuild = {
    useExact: await page.getByRole("button", { name: "使用", exact: true }).count(),
    buildExact: await page.getByRole("button", { name: "打造", exact: true }).count(),
  };
  summary.checks.watchingPanel = await page.locator('[data-use-watching="true"]').count();
  summary.checks.delegateLabels = [];
  const paths = page.locator('[class*="successPathCard"]');
  await paths.first().waitFor({ timeout: 8000 }).catch(() => {});
  for (let i = 0; i < Math.min(await paths.count(), 5); i++) {
    summary.checks.delegateLabels.push((await paths.nth(i).innerText()).replace(/\s+/g, " ").trim());
  }
  await shot(page, "grok-redesign-01-home.png");

  // —— Watching / unread cards
  summary.checks.unreadButtons = await page.locator("[data-watching-alert]").count();
  summary.checks.watchingHead = (
    await page.locator('[class*="inlineAssistCard"]').first().innerText().catch(() => "")
  ).slice(0, 120);
  await shot(page, "grok-redesign-02-watching-unread.png");

  // —— Trust card on a session with completed answers
  // Re-seed session and reload so tasks render
  await page.evaluate((sessionId) => {
    localStorage.setItem("financial-bot-session-v1", sessionId);
    localStorage.setItem("financial-bot-product-mode-v1", "use");
  }, trustSession);
  await page.goto(`${base}/bot`, { waitUntil: "domcontentloaded", timeout: 60000 });
  await page.waitForTimeout(3500);

  // Prefer clicking a chat in roster if session didn't restore
  const trust = page.locator('[class*="trustCard"], [class*="resultTrustStrip"]');
  if (!(await trust.count())) {
    // Try open roster chat history / pick a known query text
    const hit = page.getByText("腾讯目前是否值得投资").or(page.getByText("今天腾讯涨势如何")).or(page.getByText("贵州茅台贵不贵"));
    if (await hit.count()) {
      await hit.first().scrollIntoViewIfNeeded().catch(() => {});
      await page.waitForTimeout(400);
    }
  }
  // Scroll chat to find trust card
  const chat = page.locator('[class*="chat"]').first();
  if (await chat.count()) {
    for (let i = 0; i < 8; i++) {
      if (await trust.count()) break;
      await chat.evaluate((el) => { el.scrollTop = Math.min(el.scrollHeight, el.scrollTop + 700); });
      await page.waitForTimeout(250);
    }
  }
  summary.checks.trustCardCount = await page.locator('[class*="trustCard"]').count();
  summary.checks.trustStripCount = await page.locator('[class*="resultTrustStrip"]').count();
  if (await page.locator('[class*="trustCard"]').count()) {
    await page.locator('[class*="trustCard"]').first().scrollIntoViewIfNeeded();
    await page.waitForTimeout(300);
  }
  await shot(page, "grok-redesign-03-trust-card.png");

  // —— Adjust capabilities (demoted build entry)
  const adjust = page.getByRole("button", { name: "调整能力", exact: true });
  if (await adjust.count()) {
    await adjust.first().click({ force: true });
    await page.waitForTimeout(800);
  }
  summary.checks.modeAfterAdjust = await shell.getAttribute("data-product-mode").catch(() => null);
  summary.checks.backToChat = await page.getByRole("button", { name: "回到对话", exact: true }).count();
  summary.checks.expandInBuild = await page.locator('button:has-text("⤢")').count();
  await shot(page, "grok-redesign-04-adjust-entry.png");
} finally {
  await browser.close();
}

fs.writeFileSync(path.join(out, "grok-redesign-summary.json"), JSON.stringify(summary, null, 2));
console.log(JSON.stringify(summary, null, 2));
