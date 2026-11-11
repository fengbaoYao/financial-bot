import { chromium } from "playwright";
import { mkdirSync, writeFileSync } from "fs";
import { join } from "path";

const origin = process.env.ORIGIN || "https://casino-commodity-persons-cams.trycloudflare.com";
const outDir = join(process.cwd(), "output/fb-product/mobile-audit-after");
mkdirSync(outDir, { recursive: true });

const browser = await chromium.launch({ headless: true });
const page = await browser.newPage({
  viewport: { width: 390, height: 844 },
  deviceScaleFactor: 2,
  isMobile: true,
  hasTouch: true,
});

async function measure() {
  return page.evaluate(() => {
    const rect = (el) => {
      const r = el.getBoundingClientRect();
      return {
        w: Math.round(r.width),
        h: Math.round(r.height),
        text: (el.innerText || "").replace(/\s+/g, " ").slice(0, 100),
      };
    };
    const visible = (el) => {
      if (!el) return false;
      const s = getComputedStyle(el);
      if (s.display === "none" || s.visibility === "hidden" || Number(s.opacity) === 0) return false;
      const r = el.getBoundingClientRect();
      return r.width > 0 && r.height > 0;
    };
    const byText = (re) =>
      [...document.querySelectorAll("a,button,span,strong,nav,header,div")].filter((el) => re.test((el.innerText || "").trim()));
    const firstVisibleText = (re) => {
      const el = byText(re).find(visible);
      return el ? rect(el) : null;
    };
    const verticalish = [...document.querySelectorAll("button,a,span,strong")].filter((el) => {
      const t = (el.innerText || "").trim();
      if (!/(显示面板|隐藏面板|Show panel|Hide panel)/.test(t)) return false;
      const r = el.getBoundingClientRect();
      return visible(el) && r.height > r.width * 1.5 && r.width < 48;
    }).map((el) => ({ text: el.innerText.trim().slice(0, 40), ...rect(el) }));

    const moreBtn = [...document.querySelectorAll("button")].find((el) => /更多操作|More actions/.test(el.getAttribute("aria-label") || ""));
    const menuBtn = [...document.querySelectorAll("button")].find((el) => /菜单|Menu|关闭|Close/.test(el.getAttribute("aria-label") || ""));
    const modes = [...document.querySelectorAll("button")].filter((el) => /^(快速|深度|Quick|Deep)$/.test((el.innerText || "").trim()) && visible(el)).map(rect);

    return {
      url: location.href,
      enOrClassicInBar: !!(
        [...document.querySelectorAll("header a, header button")].find((el) => visible(el) && /^(EN|中文|经典研究台|Classic desk)$/.test((el.innerText || "").trim()))
      ),
      hamburger: menuBtn && visible(menuBtn) ? rect(menuBtn) : null,
      brand: firstVisibleText(/^Financial( Bot)?$/),
      brandShort: firstVisibleText(/^Financial$/),
      panel: firstVisibleText(/显示面板|隐藏面板|Show panel|Hide panel/),
      more: moreBtn && visible(moreBtn) ? rect(moreBtn) : null,
      modes,
      verticalish,
      disclaimerBottom: (() => {
        const el = [...document.querySelectorAll("p")].find((p) => /不构成投资建议|not investment advice/i.test(p.innerText || ""));
        if (!el) return null;
        const r = el.getBoundingClientRect();
        return { bottom: Math.round(r.bottom), viewport: window.innerHeight, gap: Math.round(window.innerHeight - r.bottom) };
      })(),
    };
  });
}

const shots = [];
async function shot(name, pathUrl, after) {
  await page.goto(origin + pathUrl, { waitUntil: "domcontentloaded", timeout: 90000 });
  await page.waitForTimeout(2200);
  if (after) await after();
  const file = join(outDir, name);
  await page.screenshot({ path: file, fullPage: false });
  const metrics = await measure();
  shots.push({ name, pathUrl, metrics });
  console.log(name, JSON.stringify(metrics));
}

await shot("after-landing-390.png", "/landing?lang=zh");
await shot("after-bot-use-390.png", "/bot?mode=use&lang=zh");
await shot("after-bot-build-390.png", "/bot?mode=build&lang=zh");
await shot("after-work-390.png", "/work?lang=zh");

await page.goto(origin + "/bot?mode=use&lang=zh", { waitUntil: "domcontentloaded", timeout: 90000 });
await page.waitForTimeout(1800);
const more = page.locator('button[aria-label="更多操作"], button[aria-label="More actions"]');
if (await more.count()) {
  await more.first().click();
  await page.waitForTimeout(500);
  await page.screenshot({ path: join(outDir, "after-bot-more-390.png"), fullPage: false });
  shots.push({ name: "after-bot-more-390.png", metrics: await measure() });
  console.log("after-bot-more-390.png", "menu-open");
}

writeFileSync(join(outDir, "metrics.json"), JSON.stringify(shots, null, 2));
await browser.close();
console.log("DONE", outDir);
