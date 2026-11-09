#!/usr/bin/env node
/**
 * Regression check for the bot shell height chain + transcript scrolling.
 *
 * Guards `components/financial-bot/financial-bot-styles.ts`: when a class name
 * exists in both `financial-bot.module.css` and `financial-bot-ux.module.css`,
 * the merge must keep BOTH hashes. Letting the ux module win silently disables
 * every base rule for that name (`shell` height chain, `stageColumn` flex,
 * `.stageColumn .chat { overflow-y: auto }`) and a long conversation stops
 * scrolling while an invalid DOM prop can also light up the dev overlay badge.
 *
 * Usage (dev server on :3000, a session with a long transcript):
 *   node scripts/fb-bot-scroll-check.mjs
 * Env: FB_BASE, FB_SESSION, FB_HEADFUL
 */
import fs from "node:fs";
import path from "node:path";
import { chromium } from "playwright";

const base = process.env.FB_BASE || "http://127.0.0.1:3000";

/** Pick the busiest conversation instead of hard-coding a fixture id.
 *  A hard-coded id rots the moment that conversation is deleted. */
async function pickSession() {
  if (process.env.FB_SESSION) return { id: process.env.FB_SESSION, tasks: 99 };
  try {
    const response = await fetch(`${base}/api/sessions?limit=100`);
    const payload = await response.json();
    const sessions = Array.isArray(payload.sessions) ? payload.sessions : [];
    const richest = [...sessions].sort((a, b) => (b.task_count || 0) - (a.task_count || 0))[0];
    return richest ? { id: richest.id, tasks: richest.task_count || 0 } : { id: null, tasks: 0 };
  } catch {
    return { id: null, tasks: 0 };
  }
}

const picked = await pickSession();
const session = picked.id;
if (!session) {
  console.error("No conversation available: start one research task first, or set FB_SESSION.");
  process.exit(1);
}
const failures = [];
const checks = {};

function expect(name, ok, detail) {
  checks[name] = { ok, ...detail };
  if (!ok) failures.push(`${name}: ${JSON.stringify(detail)}`);
}

const browser = await chromium.launch({ headless: !process.env.FB_HEADFUL });
try {
  const context = await browser.newContext({ viewport: { width: 1440, height: 900 }, locale: "zh-CN" });
  const page = await context.newPage();
  const consoleErrors = [];
  page.on("console", (msg) => {
    if (msg.type() === "error") consoleErrors.push(msg.text());
  });
  page.on("pageerror", (error) => consoleErrors.push(`[pageerror] ${error.message}`));

  await page.addInitScript((sessionId) => {
    try {
      localStorage.setItem("financial-bot-product-mode-v1", "use");
      localStorage.setItem("financial-bot-session-v1", sessionId);
    } catch {}
  }, session);

  // Opening a page must never materialize a conversation (welcome/state/read used
  // to create thread rows, so every fresh tab added an empty "未命名对话").
  const countSessions = async () => {
    try {
      const response = await fetch(`${base}/api/sessions?limit=100`);
      if (!response.ok) return null;
      const payload = await response.json();
      return Array.isArray(payload.sessions) ? payload.sessions.length : null;
    } catch {
      return null;
    }
  };
  const sessionsBefore = await countSessions();

  await page.goto(`${base}/bot`, { waitUntil: "domcontentloaded", timeout: 60000 });
  await page.waitForTimeout(3500);

  const sessionsAfter = await countSessions();
  if (sessionsBefore !== null && sessionsAfter !== null) {
    expect("opening the page creates no empty conversation", sessionsAfter === sessionsBefore, {
      before: sessionsBefore,
      after: sessionsAfter,
    });
  }

  const chat = page.locator('[class*="chat"]').first();
  await chat.waitFor({ timeout: 15000 });

  const metrics = await page.evaluate(() => {
    const chatEl = document.querySelector('[class*="chat"]');
    const stage = chatEl.parentElement;
    const shell = document.querySelector('[class*="shell"]');
    const badge = document.querySelector("nextjs-portal")?.shadowRoot?.querySelector("[data-next-badge]");
    const before = chatEl.scrollTop;
    chatEl.scrollTop = chatEl.scrollHeight;
    const moved = chatEl.scrollTop > before;
    const chatStyle = getComputedStyle(chatEl);
    return {
      viewport: window.innerHeight,
      documentHeight: document.documentElement.scrollHeight,
      shellHeight: Math.round(shell?.getBoundingClientRect().height ?? 0),
      stageDisplay: stage ? getComputedStyle(stage).display : null,
      chatOverflowY: chatStyle.overflowY,
      chatClientHeight: chatEl.clientHeight,
      chatScrollHeight: chatEl.scrollHeight,
      chatScrolledTo: chatEl.scrollTop,
      chatCanScroll: moved,
      devBadgeError: badge?.getAttribute("data-error") ?? null,
    };
  });

  expect("shell fits viewport (100dvh)", metrics.shellHeight === metrics.viewport, {
    shellHeight: metrics.shellHeight,
    viewport: metrics.viewport,
  });
  expect("page itself does not scroll", metrics.documentHeight <= metrics.viewport, {
    documentHeight: metrics.documentHeight,
    viewport: metrics.viewport,
  });
  expect("stage column is a flex container", metrics.stageDisplay === "flex", {
    display: metrics.stageDisplay,
  });
  expect("transcript is a scroll container", metrics.chatOverflowY === "auto", {
    overflowY: metrics.chatOverflowY,
  });
  if (picked.tasks > 0) {
    expect(
      "long transcript overflows and can be scrolled",
      metrics.chatScrollHeight > metrics.chatClientHeight && metrics.chatCanScroll,
      {
        scrollHeight: metrics.chatScrollHeight,
        clientHeight: metrics.chatClientHeight,
        scrolledTo: metrics.chatScrolledTo,
        tasks: picked.tasks,
      },
    );
  } else {
    console.log("note: the selected conversation has no tasks yet — skipping the overflow assertion");
  }

  if (metrics.devBadgeError !== null) {
    expect("no React dev-overlay error", metrics.devBadgeError === "false", {
      devBadgeError: metrics.devBadgeError,
    });
  }
  const reactPropErrors = consoleErrors.filter((line) => line.includes("React does not recognize"));
  expect("no invalid DOM prop warnings", reactPropErrors.length === 0, { reactPropErrors });

  const outDir = path.resolve("output/fb-checks");
  fs.mkdirSync(outDir, { recursive: true });
  const shot = path.join(outDir, "bot-scroll-check.png");

  // The roster's chat history is collapsed by default; when open, every row must
  // keep a readable title width (a broad `.botRoster button` rule once turned the
  // rows into grids and squeezed titles to ~40px).
  const activeBot = page.locator('[class*="botRosterActive"]').first();
  if (await activeBot.count()) {
    // The current assistant's conversations are expanded by default; only toggle
    // when this session has no chats at all.
    let rowWidths = await page.evaluate(() =>
      [...document.querySelectorAll('[class*="rosterChatRow"] button:first-child')]
        .map((el) => Math.round(el.getBoundingClientRect().width)),
    );
    if (!rowWidths.length) {
      await activeBot.click().catch(() => {});
      await page.waitForTimeout(600);
      rowWidths = await page.evaluate(() =>
        [...document.querySelectorAll('[class*="rosterChatRow"] button:first-child')]
          .map((el) => Math.round(el.getBoundingClientRect().width)),
      );
    }
    if (rowWidths.length) {
      expect("chat history rows keep a readable title", rowWidths.every((width) => width > 120), { rowWidths });
    }
  }

  // Dark theme: paragraphs inside answers are styled by a global `.markdown-body`
  // rule; they must not fall back to the light-theme ink on a dark bubble.
  const darkContext = await browser.newContext({ viewport: { width: 1440, height: 900 }, locale: "zh-CN" });
  const darkPage = await darkContext.newPage();
  await darkPage.addInitScript((sessionId) => {
    try {
      localStorage.setItem("financial-bot-language", "zh");
      localStorage.setItem("financial-bot-session-v1", sessionId);
      localStorage.setItem("financial-bot-theme", "dark");
    } catch {}
  }, session);
  await darkPage.goto(`${base}/bot`, { waitUntil: "domcontentloaded", timeout: 60000 });
  await darkPage.waitForTimeout(3500);
  const darkContrast = await darkPage.evaluate(() => {
    const luminance = (rgb) => {
      const [r, g, b] = rgb.match(/\d+/g).slice(0, 3).map(Number).map((v) => {
        const c = v / 255;
        return c <= 0.03928 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4;
      });
      return 0.2126 * r + 0.7152 * g + 0.0722 * b;
    };
    const ratio = (a, b) => {
      const [light, dark] = [luminance(a), luminance(b)].sort((x, y) => y - x);
      return (light + 0.05) / (dark + 0.05);
    };
    const paragraph = document.querySelector('[class*="agentBubble"] .markdown-body p');
    const bubble = paragraph?.closest('[class*="agentBubble"]');
    if (!paragraph || !bubble) return null;
    return {
      text: getComputedStyle(paragraph).color,
      background: getComputedStyle(bubble).backgroundColor,
      ratio: Number(ratio(getComputedStyle(paragraph).color, getComputedStyle(bubble).backgroundColor).toFixed(2)),
    };
  });
  if (darkContrast) {
    expect("dark theme answer text is readable (contrast > 4.5)", darkContrast.ratio > 4.5, darkContrast);
  }
  await darkPage.screenshot({ path: path.join(outDir, "bot-dark-check.png") }).catch(() => {});
  await darkContext.close();

  await page.screenshot({ path: shot });

  console.log(JSON.stringify({ base, session, at: new Date().toISOString(), metrics, checks, shot, consoleErrors }, null, 2));
} finally {
  await browser.close();
}

if (failures.length) {
  console.error(`\nFAIL (${failures.length})`);
  for (const failure of failures) console.error(` - ${failure}`);
  process.exit(1);
}
console.log("\nPASS — shell height chain + transcript scrolling are intact.");
