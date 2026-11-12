import { NextResponse } from "next/server";
import { proxyJson } from "../_upstream";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

function errorDetail(payload: unknown, raw: string): string {
  if (payload && typeof payload === "object" && "detail" in payload) {
    const detail = (payload as { detail?: unknown }).detail;
    if (typeof detail === "string" && detail) return detail.slice(0, 500);
  }
  return raw.slice(0, 500) || "upstream error";
}

function pickFields(value: unknown, fields: readonly string[]): Record<string, unknown> | null {
  if (!value || typeof value !== "object" || Array.isArray(value)) return null;
  const source = value as Record<string, unknown>;
  return Object.fromEntries(fields.filter((field) => field in source).map((field) => [field, source[field]]));
}

const BOT_SUMMARY_FIELDS = [
  "id", "name", "title", "busy", "activity", "mainThreadId", "threadId",
  "modelSelection", "computer", "alwaysAllow", "tasks",
] as const;
const TASK_SUMMARY_FIELDS = [
  "threadId", "title", "busy", "activity", "unread", "updatedAt",
  "lastThreadOrderAt", "pinned", "archivedAt", "modelSelection", "followsBotModel",
] as const;

function compactBotsPayload(payload: unknown): unknown {
  if (!payload || typeof payload !== "object" || Array.isArray(payload)) return payload;
  const record = payload as Record<string, unknown>;
  if (!Array.isArray(record.bots)) return payload;
  return {
    ...(typeof record.ok === "boolean" ? { ok: record.ok } : {}),
    bots: record.bots.flatMap((value) => {
      const bot = pickFields(value, BOT_SUMMARY_FIELDS);
      if (!bot || typeof bot.id !== "string") return [];
      if (Array.isArray(bot.tasks)) {
        bot.tasks = bot.tasks.flatMap((task) => {
          const summary = pickFields(task, TASK_SUMMARY_FIELDS);
          return summary && typeof summary.threadId === "string" ? [summary] : [];
        });
      }
      return [bot];
    }),
  };
}

export async function GET() {
  try {
    const { upstream, payload, raw } = await proxyJson("/api/mausbot/bots", { timeoutMs: 20_000 });
    if (!upstream.ok) {
      return NextResponse.json(
        { ok: false, detail: errorDetail(payload, raw) },
        { status: upstream.status },
      );
    }
    if (!payload || typeof payload !== "object" || Array.isArray(payload) || !Array.isArray((payload as Record<string, unknown>).bots)) {
      return NextResponse.json({ ok: false, detail: "invalid upstream bots payload" }, { status: 502 });
    }
    return NextResponse.json(compactBotsPayload(payload), { status: 200 });
  } catch (error) {
    const detail = error instanceof Error ? error.message : "MausBot proxy unreachable";
    return NextResponse.json({ ok: false, detail }, { status: 503 });
  }
}
