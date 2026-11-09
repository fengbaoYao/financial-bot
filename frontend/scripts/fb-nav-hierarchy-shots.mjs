import { chromium } from "playwright";
import { mkdirSync } from "fs";
import { join } from "path";

const base = process.env.FB_BASE || "http://127.0.0.1:3000";
const outDir = process.env.FB_OUT || join(process.cwd(), "output/fb-product");
mkdirSync(outDir, { recursive: true });

const browser = await chromium.launch({ headless: true });
const page = await browser.newPage({ viewport: { width: 1440, height: 900 } });

async function shot(name, url, after) {
  await page.goto(url, { waitUntil: "networkidle", timeout: 60000 });
  await page.waitForTimeout(1200);
  if (after) await after(page);
  const path = join(outDir, name);
  await page.screenshot({ path, fullPage: false });
  console.log("shot", name, "→", path);
  return path;
}

await shot("nav-01-home-landing.png", `${base}/landing?lang=zh`);

await shot("nav-02-bot-use.png", `${base}/bot?mode=use&lang=zh`, async (p) => {
  // clear stored build preference effect already handled by URL
  await p.waitForSelector('[aria-label="产品层级"], [aria-label="Product hierarchy"]', { timeout: 15000 });
});

await shot("nav-03-bot-adjust.png", `${base}/bot?mode=build&lang=zh`, async (p) => {
  await p.waitForSelector('[aria-label="产品层级"], [aria-label="Product hierarchy"]', { timeout: 15000 });
  // click 回到使用 then 调整能力 to verify round-trip if buttons exist
  const back = p.getByRole("button", { name: /回到使用|Back to Use/ });
  if (await back.count()) {
    await back.first().click();
    await p.waitForTimeout(600);
  }
  const adjust = p.getByRole("button", { name: /调整能力|Adjust/ });
  if (await adjust.count()) {
    await adjust.first().click();
    await p.waitForTimeout(800);
  }
});

await shot("nav-04-work-classic.png", `${base}/work?lang=zh`, async (p) => {
  await p.waitForSelector(".workspace-product-crumb, .workspace-header", { timeout: 15000 });
});

// read crumb texts
await page.goto(`${base}/bot?mode=use&lang=zh`, { waitUntil: "networkidle" });
await page.waitForTimeout(800);
const useCrumb = await page.locator('[aria-label="产品层级"]').innerText().catch(() => "");
await page.goto(`${base}/bot?mode=build&lang=zh`, { waitUntil: "networkidle" });
await page.waitForTimeout(800);
const buildCrumb = await page.locator('[aria-label="产品层级"]').innerText().catch(() => "");
await page.goto(`${base}/landing?lang=zh`, { waitUntil: "networkidle" });
const cta = await page.locator("a.navBeta, a[class*='navBeta'], a[class*='primary']").first().getAttribute("href").catch(() => null);
const heroCtas = await page.locator("main a").evaluateAll((els) =>
  els.filter((e) => /开始使用|Start using/.test(e.textContent || "")).map((e) => e.getAttribute("href"))
);

console.log(JSON.stringify({ useCrumb, buildCrumb, heroCtas, sampleCta: cta }, null, 2));
await browser.close();
