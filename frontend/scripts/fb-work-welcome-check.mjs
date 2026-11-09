import { chromium } from "playwright";
import { mkdirSync, writeFileSync } from "fs";
import { join } from "path";

const base = process.env.FB_BASE || "http://127.0.0.1:3000";
const outDir = process.env.FB_OUT || join(process.cwd(), "output/fb-product");
mkdirSync(outDir, { recursive: true });

const historyTitle = "海光信息跟踪";
const historyUser = "看看海光信息最近消息";
const historyAgent = "这是历史会话里的研究回复摘要。";
const welcomeText = "你好，我是 financial-work";

const seeded = [
  {
    id: "session-history-1",
    title: historyTitle,
    pinned: false,
    createdAt: Date.now() - 86_400_000,
    updatedAt: Date.now() - 3_600_000,
    messages: [
      {
        id: "msg-h-user",
        role: "user",
        content: historyUser,
      },
      {
        id: "msg-h-agent",
        role: "agent",
        content: historyAgent,
      },
    ],
  },
  {
    id: "session-history-2",
    title: "持仓复盘",
    pinned: false,
    createdAt: Date.now() - 172_800_000,
    updatedAt: Date.now() - 86_400_000,
    messages: [
      { id: "msg-h2-user", role: "user", content: "分析我的持仓" },
      { id: "msg-h2-agent", role: "agent", content: "持仓分析占位回复。" },
    ],
  },
];

const browser = await chromium.launch({ headless: true });
const page = await browser.newPage({ viewport: { width: 1440, height: 900 } });

await page.addInitScript((payload) => {
  window.localStorage.setItem("ashare-agent-sessions-v1", JSON.stringify(payload));
}, seeded);

const report = { at: new Date().toISOString(), base };

await page.goto(`${base}/work?lang=zh`, { waitUntil: "networkidle", timeout: 90_000 });
await page.waitForTimeout(2000);

const welcomeCard = page.locator(".gpt-welcome-card");
const emptyDesk = await welcomeCard.count();
const welcomeHeading = emptyDesk ? (await welcomeCard.locator("h3").innerText()).trim() : null;
const bodyHasHistoryReply = await page.getByText(historyAgent).count();
const leftHasHistory = await page.getByText(historyTitle).count();

report.defaultEntry = {
  welcomeCardVisible: emptyDesk > 0,
  welcomeHeading,
  historyReplyVisibleOnDefault: bodyHasHistoryReply > 0,
  leftRailHasHistory: leftHasHistory > 0,
  chatClass: await page.locator(".gpt-chat").getAttribute("class"),
};

await page.screenshot({ path: join(outDir, "work-welcome-01-default.png"), fullPage: false });

// Open historical session from left rail
await page.getByText(historyTitle, { exact: true }).first().click();
await page.waitForTimeout(1500);

const afterWelcomeCard = await page.locator(".gpt-welcome-card").count();
const welcomeBubble = await page.getByText(welcomeText).count();
const historyVisible = await page.getByText(historyAgent).count();
const userVisible = await page.getByText(historyUser).count();

report.afterHistorySelect = {
  welcomeCardVisible: afterWelcomeCard > 0,
  welcomeBubbleOrTextVisible: welcomeBubble > 0,
  historyReplyVisible: historyVisible > 0,
  historyUserVisible: userVisible > 0,
  chatClass: await page.locator(".gpt-chat").getAttribute("class"),
};

await page.screenshot({ path: join(outDir, "work-welcome-02-history.png"), fullPage: false });

// Landing still opens
const landing = await page.goto(`${base}/landing?lang=zh`, { waitUntil: "networkidle", timeout: 90_000 });
await page.waitForTimeout(1000);
const landingHero = await page.locator("#hero-title").count();
report.landing = {
  status: landing?.status(),
  heroVisible: landingHero > 0,
  title: landingHero ? (await page.locator("#hero-title").innerText()).trim() : null,
};
await page.screenshot({ path: join(outDir, "work-welcome-03-landing-still-ok.png"), fullPage: false });

const root = await page.goto(`${base}/`, { waitUntil: "networkidle", timeout: 90_000 });
await page.waitForTimeout(800);
report.root = {
  finalUrl: page.url(),
  status: root?.status(),
  heroVisible: (await page.locator("#hero-title").count()) > 0,
};
await page.screenshot({ path: join(outDir, "work-welcome-04-root.png"), fullPage: false });

const ok =
  report.defaultEntry.welcomeCardVisible &&
  !report.defaultEntry.historyReplyVisibleOnDefault &&
  report.defaultEntry.leftRailHasHistory &&
  report.afterHistorySelect.welcomeBubbleOrTextVisible &&
  report.afterHistorySelect.historyReplyVisible &&
  report.landing.heroVisible &&
  report.root.heroVisible;

report.ok = ok;
writeFileSync(join(outDir, "work-welcome-summary.json"), JSON.stringify(report, null, 2));
console.log(JSON.stringify(report, null, 2));
if (!ok) {
  console.error("SELF-CHECK FAILED");
  process.exit(1);
}
await browser.close();
