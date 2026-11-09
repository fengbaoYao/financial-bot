"use client";

import { useEffect, useState } from "react";
import { FinancialBotWorkbench } from "@/components/financial-bot/workbench";
import { MausbotShell } from "@/components/financial-bot/mausbot-shell";

export function BotPageClient() {
  const [mode, setMode] = useState<"loading" | "mausbot" | "financial">("loading");

  useEffect(() => {
    const sp = new URLSearchParams(window.location.search);
    const useMaus =
      process.env.NEXT_PUBLIC_USE_MAUSBOT === "1" || sp.get("mausbot") === "1";
    setMode(useMaus ? "mausbot" : "financial");
  }, []);

  if (mode === "loading") return null;
  if (mode === "mausbot") return <MausbotShell />;
  return <FinancialBotWorkbench />;
}
