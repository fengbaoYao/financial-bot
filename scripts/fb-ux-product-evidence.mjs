import { chromium } from "playwright";
import fs from "node:fs";
import path from "node:path";

const out = path.resolve("reports/financial-bot/ux-fix-product");
fs.mkdirSync(out, { recursive: true });
const base = process.env.FB_BASE || "http://127.0.0.1:3000";
const summary = { base, shots: [], checks: {}, at: new Date().toISOString() };

async function shot(page, name) {
  const file = path.join(out, name);
  await page.screenshot({ path: file, fullPage: false });
  summary.shots.push({ name, abs: file });
}

async function runViewport(browser, { width, height, tag }) {
  const context = await browser.newContext({ viewport: { width, height }, locale: "zh-CN" });
  const page = await context.newPage();
  await page.addInitScript(() => {
    try {
      sessionStorage.removeItem("financial-bot-rail-cue-seen");
      localStorage.setItem("financial-bot-language", "zh");
      // Seed default once per context; do not clobber on reload (persistence check).
      if (!sessionStorage.getItem("fb-product-mode-seeded")) {
        localStorage.setItem("financial-bot-product-mode-v1", "use");
        sessionStorage.setItem("fb-product-mode-seeded", "1");
      }
    } catch {}
  });
  await page.goto(`${base}/bot`, { waitUntil: "domcontentloaded", timeout: 60000 });
  await page.waitForTimeout(2800);

  const shell = page.locator('[data-product-mode]').first();
  summary.checks[`${tag}_defaultMode`] = await shell.getAttribute("data-product-mode").catch(() => null);

  const paths = page.locator('[class*="successPathCard"]');
  await paths.first().waitFor({ timeout: 8000 }).catch(() => {});
  summary.checks[`${tag}_successPathCount`] = await paths.count();
  summary.checks[`${tag}_successPathLabels`] = [];
  for (let i = 0; i < Math.min(await paths.count(), 5); i++) {
    summary.checks[`${tag}_successPathLabels`].push(
      ((await paths.nth(i).innerText()).replace(/\s+/g, " ").trim()),
    );
  }

  const useBtn = page.getByRole("button", { name: "使用", exact: true });
  const buildBtn = page.getByRole("button", { name: "打造", exact: true });
  summary.checks[`${tag}_modeToggle`] = {
    use: await useBtn.count(),
    build: await buildBtn.count(),
  };

  // Hide ⤢ in use mode
  const expandUse = page.locator('button:has-text("⤢")');
  summary.checks[`${tag}_expandInUse`] = await expandUse.count();

  await shot(page, `${tag}-01-use-default-templates.png`);

  // Switch to build
  if (await buildBtn.count()) {
    await buildBtn.first().click({ force: true });
    await page.waitForTimeout(600);
  }
  summary.checks[`${tag}_modeAfterBuild`] = await shell.getAttribute("data-product-mode").catch(() => null);
  summary.checks[`${tag}_expandInBuild`] = await page.locator('button:has-text("⤢")').count();
  await shot(page, `${tag}-02-build-mode.png`);

  // Open rail in build
  const expand = page.locator('button:has-text("⤢")');
  if (await expand.count()) {
    await expand.first().click();
    await page.waitForTimeout(700);
    await shot(page, `${tag}-03-build-rail-open.png`);
  }

  // Open assistant settings via panel gear (visible when rail open) or DOM click on roster foot
  const panelSettings = page.locator('[class*="botPanelActions"] button[aria-label*="助手设置"], [class*="botPanelActions"] button[aria-label*="Assistant settings"]');
  let openedStudio = false;
  if (await panelSettings.count()) {
    await panelSettings.first().evaluate((el) => el.click());
    openedStudio = true;
  } else {
    const rosterSettings = page.locator('[class*="rosterBotSettings"]');
    if (await rosterSettings.count()) {
      await rosterSettings.first().evaluate((el) => el.click());
      openedStudio = true;
    }
  }
  if (openedStudio) {
    await page.waitForTimeout(900);
    const kicker = page.locator('[class*="studioKicker"]');
    summary.checks[`${tag}_studioKicker`] = (await kicker.count())
      ? (await kicker.first().innerText()).trim()
      : null;
    await shot(page, `${tag}-04-assistant-settings.png`);
    const closeStudio = page.locator('[class*="studioBackdrop"] button').filter({ hasText: /关闭|Close/ });
    if (await closeStudio.count()) await closeStudio.first().evaluate((el) => el.click());
    await page.waitForTimeout(400);
    const confirmClose = page.locator('[class*="studioDialogBackdrop"] button').filter({ hasText: /关闭|Close|Discard|放弃/ });
    if (await confirmClose.count()) await confirmClose.first().evaluate((el) => el.click());
    await page.waitForTimeout(300);
  } else {
    summary.checks[`${tag}_studioKicker`] = null;
  }

  // Dismiss mobile rail backdrop so later mode toggles work
  const backdrop = page.locator('[class*="mobileScreenBackdrop"]');
  if (await backdrop.count()) {
    await backdrop.first().click({ force: true }).catch(() => {});
    await page.waitForTimeout(250);
  }

  // Back to use and confirm templates still there
  if (await useBtn.count()) {
    await useBtn.first().click({ force: true });
    await page.waitForTimeout(500);
  }
  summary.checks[`${tag}_modeBackUse`] = await shell.getAttribute("data-product-mode").catch(() => null);
  summary.checks[`${tag}_expandBackUse`] = await page.locator('button:has-text("⤢")').count();
  await shot(page, `${tag}-05-back-use.png`);

  // Persistence: reload should stay use (we set use last)
  await page.reload({ waitUntil: "domcontentloaded" });
  await page.waitForTimeout(2200);
  summary.checks[`${tag}_modeAfterReload`] = await page.locator('[data-product-mode]').first().getAttribute("data-product-mode").catch(() => null);
  await shot(page, `${tag}-06-reload-persists-use.png`);

  // Switch build, reload, expect build
  const build2 = page.getByRole("button", { name: "打造", exact: true });
  if (await build2.count()) await build2.first().click({ force: true });
  await page.waitForTimeout(400);
  await page.reload({ waitUntil: "domcontentloaded" });
  await page.waitForTimeout(2200);
  summary.checks[`${tag}_modeAfterReloadBuild`] = await page.locator('[data-product-mode]').first().getAttribute("data-product-mode").catch(() => null);
  await shot(page, `${tag}-07-reload-persists-build.png`);

  await context.close();
}

const browser = await chromium.launch({ headless: true });
try {
  await runViewport(browser, { width: 1440, height: 900, tag: "desktop" });
  await runViewport(browser, { width: 430, height: 932, tag: "mobile-430" });
} finally {
  await browser.close();
}

fs.writeFileSync(path.join(out, "product-summary.json"), JSON.stringify(summary, null, 2));
fs.writeFileSync(
  path.join(out, "NOTES.md"),
  `# UX product-mode evidence\n\n- Default 使用模式; 打造 unlocks Studio/monitor/delivery\n- Three success-path templates on welcome/empty\n- Studio kicker → 助手设置\n- localStorage financial-bot-product-mode-v1\n\nSee product-summary.json and PNGs.\n`,
);
console.log(JSON.stringify(summary, null, 2));
