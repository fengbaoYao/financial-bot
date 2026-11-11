import type { Metadata } from "next";
import { Suspense } from "react";
import { siteOrigin } from "@/lib/seo";
import { isPublicBetaRuntime } from "@/lib/research-availability";
import { LanguageProvider } from "@/components/language-provider";
import { WorkspacePreferencesProvider } from "@/components/workspace-preferences";

import { AppShell, AppShellProvider } from "@/components/app-shell";
import "./tokens.css";
import "./globals.css";

export const metadata: Metadata = {
  metadataBase: new URL(siteOrigin()),
  title: "Financial Bot",
  description: "Financial Bot — AI research workspace for stocks, news and portfolios.",
  // Public marketing pages opt in explicitly; personal workspace pages stay out of search.
  robots: { index: false, follow: true },
  icons: {
    icon: "/favicon.svg",
    shortcut: "/favicon.svg",
    apple: "/favicon.svg",
  },
};

export default async function RootLayout({ children }: { children: React.ReactNode }) {
  // Do not read cookies()/headers() here: writing the language cookie on the
  // client would refresh the tree and Safari treats the follow-up request as a
  // failed navigation ("This page could not load").
  const language = "zh";
  return (
    <html lang="zh-CN" suppressHydrationWarning>
      <body suppressHydrationWarning>
        <div className="shell">
          <div className="frame">
            <Suspense fallback={null}>
              <LanguageProvider initialLanguage={language}>
              <WorkspacePreferencesProvider initialDeepResearchAvailable={!isPublicBetaRuntime(process.env)}>
              <AppShellProvider>
                <AppShell>{children}</AppShell>
              </AppShellProvider>
              </WorkspacePreferencesProvider>
              </LanguageProvider>
            </Suspense>
          </div>
        </div>
      </body>
    </html>
  );
}
