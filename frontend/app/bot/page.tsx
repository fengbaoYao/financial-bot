import type { Metadata } from "next";
import { BotPageClient } from "./bot-page-client";

export const metadata: Metadata = {
  title: "Financial Bot · 使用",
  description: "Conversation-first research agent with real run steps, sources and user-confirmed memory.",
  robots: { index: false, follow: false },
};

export default function BotPage() {
  return <BotPageClient />;
}
