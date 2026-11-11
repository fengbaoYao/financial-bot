import { WorkspaceShell } from "@/components/workspace-shell";

/**
 * Product hierarchy (IA):
 * /work = financial-work（研究台：自选/持仓/行情面板），与 /bot 同权入口，不是 bot 的上级。
 * 主路径：主页 → Work(/work) 与 Bot(/bot?mode=use) 双入口。
 * Path 保留 work；对外文案用 financial-work，避免含糊的「经典研究台」。
 */
type WorkPageProps = {
  searchParams?: Promise<{ mode?: string | string[] }>;
};

export default async function WorkPage({ searchParams }: WorkPageProps) {
  const params = await searchParams;
  const mode = Array.isArray(params?.mode) ? params.mode[0] : params?.mode;
  return <WorkspaceShell labMode={mode === "lab"} />;
}
