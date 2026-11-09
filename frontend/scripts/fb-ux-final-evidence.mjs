import { chromium } from "playwright";
import fs from "node:fs";
import path from "node:path";

const out = path.resolve("reports/financial-bot/ux-fix-final");
fs.mkdirSync(out, { recursive: true });
const base = process.env.FB_BASE || "http://127.0.0.1:3000";
const summary = { base, shots: [], checks: {}, at: new Date().toISOString() };

async function shot(page, name) {
  const file = path.join(out, name);
  await page.screenshot({ path: file, fullPage: false });
  summary.shots.push(name);
}

async function runViewport(browser, { width, height, tag }) {
  const context = await browser.newContext({ viewport: { width, height }, locale: "zh-CN" });
  const page = await context.newPage();
  await page.addInitScript(() => {
    try { sessionStorage.removeItem("financial-bot-rail-cue-seen"); } catch {}
    try { localStorage.setItem("financial-bot-language", "zh"); } catch {}
  });
  await page.goto(`${base}/bot`, { waitUntil: "domcontentloaded", timeout: 60000 });
  await page.waitForTimeout(2500);

  const hint = page.locator('[class*="composerDataHint"]');
  summary.checks[`${tag}_hint`] = {
    count: await hint.count(),
    text: (await hint.count()) ? (await hint.first().innerText()).trim() : null,
  };
  const cue = page.locator('[class*="railCue"]:not([class*="railCueWrap"]):not([class*="railCueDismiss"])');
  summary.checks[`${tag}_cue`] = {
    count: await cue.count(),
    text: (await cue.count()) ? (await cue.first().innerText()).replace(/\s+/g, " ").trim() : null,
  };
  summary.checks[`${tag}_title`] = await page.title();
  summary.checks[`${tag}_legacy_brand_absent`] = !/Open\s*Ashare/i /* assert legacy brand gone */.test(await page.locator("body").innerText());
  await shot(page, `final-${tag}-loaded.png`);

  const expand = page.getByRole("button", { name: /监控与投递|monitor/i });
  if (await expand.count()) await expand.first().click();
  else await page.locator('button:has-text("⤢")').first().click().catch(() => {});
  await page.waitForTimeout(700);
  summary.checks[`${tag}_cueAfter`] = await page.locator('[class*="railCue"]:not([class*="railCueWrap"]):not([class*="railCueDismiss"])').count();
  await shot(page, `final-${tag}-rail-open.png`);

  if (width <= 500) {
    const backdrop = page.locator('[class*="mobileScreenBackdrop"]');
    if (await backdrop.count()) await backdrop.first().click({ force: true }).catch(() => {});
    await page.waitForTimeout(300);
    const roster = page.getByRole("button", { name: /助手列表|roster/i });
    if (await roster.count()) {
      await roster.first().click({ force: true });
      await page.waitForTimeout(500);
      await shot(page, `final-${tag}-roster.png`);
    }
  }

  // EN locale quick check on desktop
  if (tag === "desktop") {
    const enCtx = await browser.newContext({ viewport: { width, height }, locale: "en-US" });
    const enPage = await enCtx.newPage();
    await enPage.addInitScript(() => {
      try { sessionStorage.removeItem("financial-bot-rail-cue-seen"); } catch {}
      try { localStorage.setItem("financial-bot-language", "en"); } catch {}
      document.cookie = "financial-bot-language=en;path=/;max-age=31536000;SameSite=Lax";
    });
    await enPage.goto(`${base}/bot?lang=en`, { waitUntil: "domcontentloaded", timeout: 60000 });
    await enPage.waitForTimeout(2000);
    const enHint = enPage.locator('[class*="composerDataHint"]');
    summary.checks.en_hint = {
      count: await enHint.count(),
      text: (await enHint.count()) ? (await enHint.first().innerText()).trim() : null,
    };
    await enPage.screenshot({ path: path.join(out, "final-desktop-en-hint.png"), fullPage: false });
    summary.shots.push("final-desktop-en-hint.png");
    await enCtx.close();
  }

  await context.close();
}

const browser = await chromium.launch({ headless: true });
try {
  await runViewport(browser, { width: 1440, height: 900, tag: "desktop" });
  await runViewport(browser, { width: 430, height: 932, tag: "narrow-430" });
} finally {
  await browser.close();
}

fs.writeFileSync(path.join(out, "final-summary.json"), JSON.stringify(summary, null, 2));
fs.writeFileSync(path.join(out, "NOTES.md"), `# UX final pass evidence\n\nRunSnapshot gaps, disposition finalize, brand, rail cue, delayed-data hint.\nSee final-summary.json.\n`);
console.log(JSON.stringify(summary, null, 2));
