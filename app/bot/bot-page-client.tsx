"use client";

import { MausbotShell } from "@/components/financial-bot/mausbot-shell";

/** Old Financial Bot workbench entry retired; /bot always opens financial-bot shell.
 *  Keep ?mausbot=1 accepted for existing bookmarks; it is no longer required.
 */
export function BotPageClient() {
  return <MausbotShell />;
}
