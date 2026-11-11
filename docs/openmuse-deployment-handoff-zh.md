# Financial Bot 与 OpenMuse 部署交接

本文交给负责部署的 Agent。当前交付包含两个独立目录的未提交修改：`OpenAshare`（Financial Bot）和同级 `OpenMuse`。基线分别为 `b8705ad` 和 `a4944f4c`；仅复制 Git HEAD 会遗漏本次接入。部署前应与开发 Agent 核对两边完整 diff 和新增文件，并以可复现的方式传递经审核的代码，排除 `.env`、密钥、数据库和本机运行数据。不要重置、覆盖已有工作或盲目运行现有部署脚本。

## 目标与边界

- 保留现有 Work、Bot 快速和深度金融研究路径及其数据来源策略。OpenMuse 是另选的自由研究入口，使用单用户共享工作区；它不提供多用户隔离。当前共享入口会拒绝被规则识别为金融研究的问题，并提示使用快速或深度模式；因此它尚不能作为可自由调用同花顺的金融研究 Bot 交付。
- 两个服务放在同一台机器，OpenMuse 的 API 仅监听本机回环地址；Docker 容器由 OpenMuse 管理，工作区持久数据需单独备份。容器默认无网络；网页搜索是否可用要在真实任务中验证，不能假设容器可直接联网。
- **不要在公网或未经验证的反向代理后启用共享研究。** 当前回环地址检查不能证明浏览器访问者身份；现有单用户模式不能作为多租户授权。先验证独立的身份认证和私有访问边界，未完成时保持功能关闭。
- 不改动现有公网服务、隧道、端口、系统 Docker 或防火墙来“试一试”。先盘点服务和资源，选择独立目录、用户和未占用端口，准备配置与数据备份和回滚方案；生产切换另行遵守现有发布流程。

## 依赖与配置

1. 确认目标机可用空间、内存、Node.js 22、Python 3.12、pnpm 版本及 Docker 引擎。当前开发机未完成真实 Docker 验证；目标机环境以部署时现场检查为准。建议使用经验证的专用 rootless Docker 和私有 socket；不要开放 Docker TCP 端口或把用户加入特权 docker 组。确认 cgroup 资源限制生效，镜像及依赖安装会产生资源和网络开销。
2. 在 OpenMuse 目录安装锁文件对应依赖，执行 `pnpm build:server`，并按仓库文档运行 `docker build -t openmuse-computer:local apps/computer`。使用单独持久目录保存 `DATA_DIR`；先验证真实 Docker 引擎、镜像和容器隔离配置，再启动服务。
3. 为 OpenMuse 配置独立的仅本机可读环境文件：`TASKS_ONLY=true`、`WORKSPACE_MODE=live`、`HOST=127.0.0.1`、未占用的 `PORT`、`AGENT_BACKEND=model`、`MODEL=openai/<实际模型名>`、`OPENAI_BASE_URL`、`OPENAI_API_KEY`、`OPENAI_CHAT_COMPLETIONS=true`（若服务商要求该接口）、`GENERAL_RESEARCH_PROFILE_ENABLED=true`、`SHARED_RESEARCH_WORKSPACE_ENABLED=true`、`COMPUTER_ENABLED=true`、`COMPUTER_IMAGE=openmuse-computer:local`。按需设置专用 Docker socket 和稳定的 `COMPUTER_DEPLOYMENT_ID`。`OPENMUSE_ACCESS_KEY` 须为至少 24 字符的随机密钥；`TOKEN_ENCRYPTION_KEY` 须为 32 随机字节的 base64。两个密钥由部署方安全生成、保存，禁止写进代码、聊天、命令记录和部署报告。任务专用模式不需要 CopilotKit 密钥；模型密钥仍需实际可用。
4. 身份与访问边界验证完成前，Financial Bot 保持 `OPENMUSE_ENABLED` 和 `OPENMUSE_SHARED_RESEARCH_ENABLED` 关闭。在隔离的单用户验证环境中，启用时设置二者为 `true`，`OPENMUSE_BASE_URL` 指向同机回环 OpenMuse 地址，`OPENMUSE_ACCESS_KEY` 与上一步一致。模型服务凭据在各服务环境中安全配置，不能默认假设现有服务的密钥可以直接复用。
5. 如果使用 Supabase/Postgres，先核对迁移 `0020` 至 `0025` 的实际状态、权限与备份；不要仅依据迁移历史盲目重复执行。SQLite 与 Postgres 的验证范围分别记录。升级现有服务前核对旧配置、数据、服务单元及可回滚版本。

## 验收

1. 分别运行两个仓库相关测试、类型检查和构建；记录代码版本、运行时版本和结果，不将模拟测试当作真实部署验收。
2. 在隔离环境启动 OpenMuse，检查 `/api/health`，再验证 Docker 容器实际运行、无网络、资源限制、共享文件跨任务保留；检查意外重启后数据与任务状态。删除测试数据前确认范围，不能删除真实工作区。
3. 从 Financial Bot 中文页面提交一个低成本的普通研究任务，核实进度、最终结果、需要补充信息时的回复、取消、刷新/重启后的恢复，以及上游异常时的提示。至少执行一次真实模型调用并记录使用的模型和结果，避免记录密钥或隐私。
4. 分别回归 Work、Bot 快速和深度金融问题，检查数据来源及降级提示。共享入口当前依赖规则识别金融问题，不能证明覆盖所有自然语言表达；发现绕过时不得宣称同花顺优先已覆盖所有入口。
5. 验证未授权外部访问无法发起任务、读取结果或操作工作区，并确认现有服务和隧道仍正常。认证或实际容器验证未通过时保持共享研究关闭，报告具体阻碍。

## 回滚

关闭 Financial Bot 的两个 OpenMuse 开关并恢复此前服务版本与配置；保留数据库、OpenMuse 数据目录、Docker 卷和备份以便调查。停止或移除任何服务、容器及数据前先确认归属，禁止清理不属于本次部署的资源。交接结果应包含实际部署版本、隔离和认证验证、模型联调结果、遗留问题及回滚方式。
