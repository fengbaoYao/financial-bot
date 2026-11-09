# SV 部署与版本核对（短说明）

## 一眼看版本
- **本地**：`git log -1 --oneline`；发布后看 `git tag --points-at HEAD`（如 `fb-deploy-20261104-1610`）。本地也会留下 `VERSION.local` / `deploy.json.local`（gitignore）。
- **线上 SV**：`cat /opt/financial-bot/VERSION` 或 `cat /opt/financial-bot/deploy.json`（含 `git_commit`、`git_tag`、`deployed_at` Asia/Shanghai）。

两边 `git_commit` / `git_tag` 一致 = 已同步。

## 怎么部署
1. 工作区干净并已打 tag：`git status` 无脏文件；`git tag fb-deploy-YYYYMMDD-HHMM`。
2. 密钥：`FB_DEPLOY_KEY=/tmp/fb_deploy_key`（或 Mac `~/.ssh/id_ed25519` 拷到该路径）。
3. 执行：`FB_DEPLOY_KEY=/tmp/fb_deploy_key ./scripts/deploy-sv.sh`
4. 脚本会：备份 `backend.bak-<short>-<ts>` / `frontend.bak-<short>-<ts>` → 同步 api + frontend-src → 写 VERSION → 重启 backend → `FB_STANDALONE=1` 构建并同步 frontend → 验活。

本仓库**不再配置** ZhiweiChen-coder/OpenAshare 远端。版本只走：本地 git tag + 硅谷 `/opt/financial-bot/VERSION`（`scripts/deploy-sv.sh`）。若以后要自己的 GitHub 备份，再单独加 `origin`。

## 自检
- `git rev-parse HEAD` == VERSION 里 `git_commit`
- `/api/bot/status` 与 `/bot` `/work` `/landing` 200
- tunnel active

## 备注
- Mac 直连 SV 有时会被掐断；可用 box（`/tmp/fb_deploy_key`）中转：先打 `.fb-release/*.tgz`，CopyToBox 后再 ssh 解压。
- `scripts/deploy-sv.sh` 要求工作区干净；打 tag 后再跑。

## 双端一致性
- 运行真相：`/opt/financial-bot/backend/api` 与本地 `api/`。
- `frontend-src` 是整仓镜像；其中 `frontend-src/api` 必须与 `backend/api` 相同。部署脚本会强制拷贝，避免旧镜像残留。
- 合并原则：本地仓库为真相源；若线上有独有修复，先拉回本地再部署。
