import test from "node:test";
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import ts from "typescript";

const source = await readFile(new URL("../lib/financial-bot/onboarding.ts", import.meta.url), "utf8");
const compiled = ts.transpileModule(source, {
  compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022 },
}).outputText;
const module = await import(`data:text/javascript;base64,${Buffer.from(compiled).toString("base64")}`);
const { optionsFor, greetingFor } = module;

function bot(name, capabilities, isDefault = false) {
  return {
    id: `bot_${name}`,
    name,
    is_default: isDefault,
    prompt_config: {
      role: "",
      markets: ["cn", "hk", "us"],
      output_style: "standard",
      capabilities,
      memory_enabled: true,
      routines_enabled: true,
      proactive_enabled: true,
    },
  };
}

const companyResearch = () => bot("公司研究", {
  web: true, filings: true, market: true, news: true, portfolio: false, watchlist: false,
});

test("a company-research bot never offers portfolio or watchlist work", () => {
  const labels = optionsFor(companyResearch(), false).map((option) => option.label);
  assert.deepEqual(labels, ["看一只股票", "查公告与财报", "追一条新闻或主题"]);
  assert.ok(!labels.some((label) => label.includes("持仓")));
  assert.ok(!labels.some((label) => label.includes("自选股")));
});

test("the greeting names the bot and its real scope", () => {
  const zh = greetingFor(companyResearch(), false);
  assert.ok(zh.startsWith("嗨，我是公司研究。"));
  assert.ok(zh.includes("公告与财报"));
  assert.ok(!zh.includes("持仓"));

  const en = greetingFor(companyResearch(), true);
  assert.ok(en.startsWith("Hi — I'm 公司研究."));
  assert.ok(en.includes("filings and financials"));
});

test("a bot with fewer capabilities gets a shorter list, not a generic one", () => {
  const filingsOnly = bot("公告机器人", {
    web: false, filings: true, market: false, news: false, portfolio: false, watchlist: false,
  });
  const labels = optionsFor(filingsOnly, false).map((option) => option.label);
  assert.deepEqual(labels, ["查公告与财报"]);
});

test("the default research bot keeps the full set of starting points", () => {
  const all = bot("研究助手", {
    web: true, filings: true, market: true, news: true, portfolio: true, watchlist: true,
  }, true);
  const labels = optionsFor(all, false).map((option) => option.label);
  assert.deepEqual(labels, ["看一只股票", "查公告与财报", "追一条新闻或主题", "检查我的持仓", "看自选股风险"]);
});

test("a bot with no tools still has one honest way in", () => {
  const empty = bot("空 Bot", {
    web: false, filings: false, market: false, news: false, portfolio: false, watchlist: false,
  });
  const options = optionsFor(empty, false);
  assert.equal(options.length, 1);
  assert.equal(options[0].templates[0].custom, true);
});
