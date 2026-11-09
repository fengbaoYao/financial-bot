import type { BotProfile } from "@/lib/financial-bot/client";

/** Onboarding logic for the first conversation, kept free of React and CSS so
 *  it can be tested directly: which questions a Bot may ask, and how it greets.
 */

export type ResearchPersona =
  | "beginner"
  | "ticker"
  | "filings"
  | "portfolio"
  | "news"
  | "pro"
  | "watchlist";

type Template = { label: string; prompt?: string; custom?: boolean };

export type OnboardOption = {
  persona: ResearchPersona;
  label: string;
  /** Said after the person picks this mode: one short line about how I will work. */
  ack: string;
  templates: Template[];
};

const LETTERS = ["A", "B", "C", "D", "E", "F"];

function capabilityText(bot: BotProfile | null, en: boolean): string {
  const caps = bot?.prompt_config?.capabilities;
  if (!caps) return "";
  const parts: string[] = [];
  if (caps.market) parts.push(en ? "quotes and technicals" : "行情与技术面");
  if (caps.filings) parts.push(en ? "filings and financials" : "公告与财报");
  if (caps.news || caps.web) parts.push(en ? "news and public sources" : "新闻与公开来源");
  if (caps.portfolio || caps.watchlist) parts.push(en ? "portfolio and watchlist risk" : "持仓与自选股风险");
  return parts.join(en ? ", " : "、");
}

/** Only offer what this Bot is actually allowed to do. */
export function optionsFor(bot: BotProfile | null, en: boolean): OnboardOption[] {
  const caps = bot?.prompt_config?.capabilities ?? {
    web: true,
    filings: true,
    market: true,
    news: true,
    portfolio: true,
    watchlist: true,
  };
  const options: OnboardOption[] = [];

  if (caps.market) {
    options.push({
      persona: "ticker",
      label: en ? "Check a stock" : "看一只股票",
      ack: en
        ? "Good — one name first. I'll confirm the identity, then bring price, valuation, recent filings and key risks together."
        : "好，先看一只股票。我会先确认标的，再把价格、估值、最近公告和主要风险放在一起。",
      templates: [
        {
          label: en ? "Kweichow Moutai sh600519" : "贵州茅台 sh600519",
          prompt: en
            ? "Analyze Kweichow Moutai sh600519: confirm the identity, then review price and valuation, recent filings and key risks. Mark sources, dates and gaps; do not give trading advice."
            : "分析贵州茅台 sh600519：先确认标的，再看当前价格与估值、最近公告和主要风险。请标注数据来源、时间和缺口，不要给买卖建议。",
        },
        {
          label: en ? "Tencent 00700.HK" : "腾讯 00700.HK",
          prompt: en
            ? "Analyze Tencent 00700.HK: confirm the identity, then review price and valuation, recent filings and key risks. Mark sources, dates and gaps; do not give trading advice."
            : "分析腾讯控股 00700.HK：先确认标的，再看当前价格与估值、最近公告和主要风险。请标注数据来源、时间和缺口，不要给买卖建议。",
        },
        {
          label: en ? "NVIDIA US.NVDA" : "英伟达 US.NVDA",
          prompt: en
            ? "Analyze NVIDIA US.NVDA: confirm the identity, then review price and valuation, recent filings and key risks. Mark sources, dates and gaps; do not give trading advice."
            : "分析英伟达 US.NVDA：先确认标的，再看当前价格与估值、最近公告和主要风险。请标注数据来源、时间和缺口，不要给买卖建议。",
        },
      ],
    });
  }

  if (caps.filings) {
    options.push({
      persona: "filings",
      label: en ? "Read filings & earnings" : "查公告与财报",
      ack: en
        ? "Alright — filings and reported numbers. Every figure comes with its filing date and period."
        : "好，看公告和财报。每个数字都会带披露日期和报告期。",
      templates: [
        {
          label: en ? "Moutai's latest filings" : "茅台最近公告",
          prompt: en
            ? "List Kweichow Moutai's sh600519 recent material filings: filing date, type, and the possible impact on the investment case; mark which items are unverified. Do not give trading advice."
            : "查贵州茅台 sh600519 最近的重要公告：列出披露日期、类型、对投资逻辑可能的影响，并标出尚未证实的信息。不要给买卖建议。",
        },
        {
          label: en ? "Tencent earnings" : "腾讯最新财报",
          prompt: en
            ? "Review Tencent 00700.HK latest reported financials: revenue, profit, cash flow and year-over-year change by segment, with reporting periods and sources. Mark gaps; do not give trading advice."
            : "查腾讯控股 00700.HK 最新财报：按分部列营收、利润、现金流与同比，标注报告期和数据来源，说明缺口，不要给买卖建议。",
        },
        {
          label: en ? "Apple 10-K" : "苹果最新 10-K",
          prompt: en
            ? "Summarize Apple US.AAPL latest 10-K: key segment revenue, margins, cash flow and risk factors, with filing dates and citations. Do not give trading advice."
            : "梳理苹果 US.AAPL 最新 10-K：分部营收、利润率、现金流与风险因素，标注申报日期和来源，不要给买卖建议。",
        },
        { label: en ? "I'll name a company" : "我自己指定公司", custom: true },
      ],
    });
  }

  if (caps.news) {
    options.push({
      persona: "news",
      label: en ? "Follow a story" : "追一条新闻或主题",
      ack: en
        ? "Okay — one thread at a time. I'll separate confirmed facts, affected names and speculation."
        : "好，追一条线。我会把已证实的事实、受影响的标的和市场推测分开写。",
      templates: [
        {
          label: en ? "Today's top three" : "今日市场最重要的事",
          prompt: en
            ? "Summarize the three most important market stories today: what happened, which US/HK/mainland China names could be affected, what is confirmed, what is speculation, and which primary sources should be checked."
            : "梳理今天市场最重要的三条新闻：发生了什么、可能影响哪些 A股/港股/美股、哪些是已证实事实、哪些只是推测，并给出继续核验需要的一手来源。",
        },
        {
          label: en ? "AI chips / technology" : "AI 芯片 / 科技",
          prompt: en
            ? "Summarize the most important AI chip and technology developments today, list potentially affected listed companies, and separate facts, inference and gaps."
            : "梳理 AI 芯片和科技行业今天最重要的变化，列出可能受影响的上市公司，并区分事实、推断和缺口。",
        },
      ],
    });
  }

  if (caps.portfolio) {
    options.push({
      persona: "portfolio",
      label: en ? "Review my portfolio" : "检查我的持仓",
      ack: en
        ? "Alright — a portfolio check: concentration, sector exposure and recent risks. No trading instructions."
        : "好，先做持仓体检：集中度、行业暴露和近期风险；不会给买卖指令。",
      templates: [
        {
          label: en ? "Start portfolio check" : "开始持仓体检",
          prompt: en
            ? "Review my portfolio for concentration, sector exposure, key risks and missing information. Do not give trading instructions."
            : "分析我的持仓：看集中度、行业暴露、主要风险和需要补充的信息。不要给买卖指令。",
        },
      ],
    });
  }

  if (caps.watchlist) {
    options.push({
      persona: "watchlist",
      label: en ? "Check my watchlist" : "看自选股风险",
      ack: en
        ? "Okay — watchlist risk: concentration, upcoming events and what needs attention."
        : "好，看自选股：集中度、近期事件和需要盯的风险。",
      templates: [
        {
          label: en ? "Watchlist risk review" : "自选股风险体检",
          prompt: en
            ? "Review my watchlist for key risks, concentration and upcoming events. Do not give trading instructions."
            : "检查我的自选股列表：汇总主要风险、集中度和近期需要关注的事件。不要给买卖指令。",
        },
      ],
    });
  }

  if (!options.length) {
    options.push({
      persona: "pro",
      label: en ? "Ask a research question" : "提一个研究问题",
      ack: en ? "Fine — ask away." : "好，直接问。",
      templates: [{ label: en ? "I'll type it" : "我自己输入", custom: true }],
    });
  }

  return options;
}


export type SuccessPath = {
  id: "rumor" | "compare" | "track";
  label: string;
  blurb: string;
  prompt: string;
  /** When true, UI should create/open a focus point, not only one-shot Q&A. */
  kind?: "ask" | "track";
};

/** Three first-screen paths — two research asks + one lasting focus. */
export function successPathsFor(en: boolean): SuccessPath[] {
  if (en) {
    return [
      {
        id: "rumor",
        kind: "ask",
        label: "Check this Tencent crash rumor",
        blurb: "Facts vs speculation, with sources and gaps.",
        prompt:
          "Someone claims Tencent Holdings (00700.HK) crashed or moved sharply. Help me verify the rumor: confirm the ticker, summarize what actually happened with timestamps, separate confirmed facts from speculation, list primary sources to check, and mark gaps. Do not give trading advice.",
      },
      {
        id: "compare",
        kind: "ask",
        label: "Compare Tencent vs Alibaba",
        blurb: "Identity, valuation, filings, risks — cited.",
        prompt:
          "Compare Tencent Holdings (00700.HK) and Alibaba (09988.HK / BABA): confirm identities, then contrast price/valuation, recent filings, and key risks side by side. Cite sources and dates; mark missing data. Do not give trading advice.",
      },
      {
        id: "track",
        kind: "track",
        label: "Watch this question; ping me later",
        blurb: "Not a one-shot — call me when evidence moves.",
        prompt:
          "I want to track a research question over time (example: Did company guidance change after the latest filing?). Help me turn it into a lasting focus: (1) confirm the exact question and any ticker, (2) say what evidence change should trigger a ping (new filing, guidance change, major move), (3) list what you can watch now vs what is still a gap, (4) remember this as a research question I am watching. Do not give trading advice; do not treat this as a one-shot answer — set up ongoing tracking.",
      },
    ];
  }
  return [
    {
      id: "rumor",
      kind: "ask",
      label: "帮我核对腾讯这句暴跌传言",
      blurb: "事实 / 推测 / 缺口，带来源。",
      prompt:
        "有人说腾讯控股（00700.HK）暴跌或异动。请帮我核对这句传言：确认标的，用时间线说明实际发生了什么，区分已证实事实与推测，列出应核对的一手来源，并标明缺口。不要给买卖建议。",
    },
    {
      id: "compare",
      kind: "ask",
      label: "帮我对一下腾讯和阿里",
      blurb: "身份、估值、公告、风险，标出来源。",
      prompt:
        "请对比腾讯控股（00700.HK）与阿里巴巴（09988.HK / BABA）：确认标的身份，并排比较价格/估值、最近公告与主要风险。请标注来源与时间，标出缺失数据。不要给买卖建议。",
    },
    {
      id: "track",
      kind: "track",
      label: "盯这个问题，有变化再叫我",
      blurb: "不是一次性问答——证据动了再提醒。",
      prompt:
        "我想长期跟踪一个研究问题（示例：最新财报/公告后，公司指引有没有改口？）。请帮我把它落成可持续关注点：(1) 确认具体问题与相关标的；(2) 说明什么证据变化时该叫我（新公告、指引改口、大幅波动等）；(3) 列出你现在能盯的与仍缺的；(4) 把这个问题记为我正在跟踪的研究问题。不要给买卖建议；不要只当一次问答——要建立持续跟踪。",
    },
  ];
}

export function greetingFor(bot: BotProfile | null, en: boolean): string {
  const name = bot?.name?.trim() || (en ? "Research Bot" : "研究助手");
  const scope = capabilityText(bot, en);
  if (en) {
    return scope
      ? `Hi — I'm ${name}. I cover ${scope}. Ask one question and I'll bring back sources, dates and gaps.`
      : `Hi — I'm ${name}. Ask one question and I'll bring back sources, dates and gaps.`;
  }
  return scope
    ? `嗨，我是${name}。我负责${scope}。你问一句，我会把来源、时间和缺口一起给你。`
    : `嗨，我是${name}。你问一句，我会把来源、时间和缺口一起给你。`;
}

