import { chromium } from "playwright";
import { mkdirSync, writeFileSync } from "fs";
import { join } from "path";

const base = process.env.FB_BASE || "http://127.0.0.1:3000";
const outDir = process.env.FB_OUT || join(process.cwd(), "output/fb-product");
mkdirSync(outDir, { recursive: true });

const browser = await chromium.launch({ headless: true });
const page = await browser.newPage({ viewport: { width: 1440, height: 900 } });

async function shot(name, url, after) {
  await page.goto(url, { waitUntil: "networkidle", timeout: 90000 });
  await page.waitForTimeout(1500);
  if (after) await after(page);
  const path = join(outDir, name);
  await page.screenshot({ path, fullPage: false });
  console.log("shot", name);
  return path;
}

const report = {};

await shot("nav-fix-01-bot-back-home.png", `${base}/bot?mode=use&lang=zh`, async (p) => {
  await p.waitForSelector('[aria-label="产品层级"], [aria-label="Product hierarchy"]', { timeout: 20000 });
  const crumb = await p.locator('[aria-label="产品层级"]').innerText();
  const homeLinks = await p.locator('a[aria-label="回主页"], a[href="/landing"]').evaluateAll((els) =>
    els.map((e) => ({ text: (e.textContent || "").trim(), href: e.getAttribute("href"), aria: e.getAttribute("aria-label"), className: e.className }))
  );
  const homeBar = await p.getByRole("link", { name: /回主页|主页|Home/ }).evaluateAll((els) =>
    els.map((e) => ({ text: (e.textContent || "").trim(), href: e.getAttribute("href"), aria: e.getAttribute("aria-label") }))
  );
  report.bot = { crumb, homeLinks, homeBar };
});

await shot("nav-fix-02-landing-work-entry.png", `${base}/landing?lang=zh`, async (p) => {
  const workLinks = await p.locator('a[href*="/work"]').evaluateAll((els) =>
    els.map((e) => ({ text: (e.textContent || "").trim(), href: e.getAttribute("href"), className: e.className }))
  );
  const primary = await p.locator('a[href*="/bot?mode=use"]').evaluateAll((els) =>
    els.map((e) => ({ text: (e.textContent || "").trim(), href: e.getAttribute("href") }))
  );
  report.landing = { workLinks, primarySample: primary.slice(0, 4) };
});

writeFileSync(join(outDir, "nav-fix-summary.json"), JSON.stringify({
  title: "nav visibility fix: bot→home + landing→work",
  at: new Date().toISOString(),
  branch: "codex/financial-bot-mvp",
  how_back_home: "在 /bot：顶栏「主页」按钮 + 面包屑第一级「主页」（青绿下划线，可点）→ /landing",
  how_to_work: "在主页 /landing：顶栏次要链「经典研究台」、英雄区次要链、页脚「经典研究台」→ /work",
  shots: [
    "output/fb-product/nav-fix-01-bot-back-home.png",
    "output/fb-product/nav-fix-02-landing-work-entry.png",
  ],
  report,
}, null, 2));

console.log(JSON.stringify(report, null, 2));
await browser.close();
