# 本地 AI Agent 部署指南

公司研究检查点仍由已连接股票仓库保存，不新增数据库或启动步骤；新版报告的已通过/计划窗口与全文综合状态要分开核对。详见[本批记录](./docs/记录/2026-10-09-IN4.20-公开线索与只读会话补修.md)。

本仓库唯一用户入口是根目录 `启动.bat`（调用 `scripts/start-daily.ps1`）。Web 与 BFF 同源运行在 `127.0.0.1:9510`；每日操作见[日常运行指南](./docs/日常运行指南.md)。

| 准备项 | 用途 | 检查 |
| --- | --- | --- |
| Windows PowerShell | 启动脚本 | `$PSVersionTable.PSVersion` |
| Python ≥ 3.11 | BFF 与工具依赖 | `python --version` |
| Node.js ≥ 18、npm | 首次构建 Web | `node --version`、`npm --version` |
| Docker Desktop / Compose | 文件、执行与搜索工具容器 | `docker compose version` |
| Ollama（按需） | 本地聊天模型和每日市场事件正文分析需要；单一启动器读取已连接股票仓库配置的回环端口 | `ollama list` |
| Git | 仓库与工作区版本管理 | `git --version` |

## 首次安装

| 步骤 | 操作 |
| --- | --- |
| 1. 获取代码 | 克隆本仓库并进入根目录 |
| 2. 建环境 | `python -m venv .conda`；`.\.conda\python.exe -m pip install -e .` |
| 3. 配置 | 从 `.env.example` 复制 `.env`；按需设置本地模型等非密钥项 |
| 4. 自检 | `.\scripts\check-env.ps1` |
| 5. 启动 | 双击 `启动.bat`；脚本会启动需要的容器、构建 Web 并打开浏览器 |
| 6. 验证 | 在页面“模型设置”配置云端密钥或选本地模型，发消息确认回复；股票技能在右栏开启 |

`.conda/python.exe` 是启动脚本要求的项目 Python 路径。云端密钥在页面“模型设置”保存到本机私有目录，不写入文档或提交。股票连接路径、状态目录与端口在“股票技能 → 高级管理 → 连接配置”设置。

| 启动参数 | 作用 |
| --- | --- |
| `-Build` | 重建工具容器镜像 |
| `-RebuildWeb` | 强制重建 Web |
| `-SkipTools` | 仅启动聊天与控制服务；文件/Python 工具暂不可用 |

| 常用操作 | 方法 |
| --- | --- |
| 日常退出 | 点页面“退出”或关闭页面，约 15 秒后自动收尾并停全部服务 |
| 页面不可用时停后端 | `.\scripts\stop-backend.ps1`；工具容器按需用 Docker 管理 |
| 查看服务状态 | 打开 `http://127.0.0.1:9510/api/status` |
| 看错误日志 | `data/logs/daily-bff.err.log`；股票后台 `data/logs/stock-service.err.log` |
| 离线恢复 | 进入股票技能面板的“恢复与待办”；步骤见[日常指南](./docs/日常运行指南.md) |

| 数据 | 位置与约定 |
| --- | --- |
| 会话 | `data/conversations.db`；研究方法选择由增量迁移保存在同一会话表，启动时自动升级 |
| 用户工作区 | `data/workspace/` |
| 模型调用账本 | `data/model-calls.sqlite` |
| 本机密钥与开关 | `data/private/`；不提交 |
| 日志 | `data/logs/` |

完整环境变量、故障排查和旧部署细节保留在[部署流程原稿](./docs/记录/部署流程-原稿.md)；其中的历史状态和示例参数须按当前代码重新核对。当前文档导航见[docs 入口](./docs/README.md)。

## 命名工作区与多目录（2026-10-07）

- 仍从根目录启动.bat 进入；改动工具代码后用 scripts/start-daily.ps1 -Build。工作区服务不提供第二条启动入口。
- conversations.db 增量迁移到工作区模型，旧会话归入默认工作区；默认 /workspace 路径保持兼容，不搬移旧文件。
- 工作台创建工作区并添加本机目录。启动器生成 data/private/workspace-compose.json，每组仅挂载本工作区目录，外部目录映射为 /roots/<root_id>。
- 默认文件/执行组随启动器启动；其他组按需经 9511 的固定 workspace_sync 操作启动。每组使用独立依赖卷与回收站，默认组保留原依赖卷及回收站。
- 各组使用独立桥接网络及 data/private/workspace-tools 下的工具凭据；BFF 页面凭据经启动器 URL fragment 交给页面后换成 HttpOnly cookie。健康核验须同时检查工作区 ID、版本、协议及鉴权，凭据不得挂载到工作区。
- 构建日志保存在 data/logs/tool-containers.log；启动器状态探测须携带页面凭据，避免将受保护的正常 BFF 误判为未就绪。
- 配置失败不替换有效映射；界面显示待生效，恢复 Docker 后点“检查并应用目录配置”。原文件读写不改为隐式复制。
- 页面退出会收尾全部组及遗留容器，保留依赖卷、工作区文件和数据库。不得对 generated Compose 使用 --volumes 删除用户依赖。

实施和环境限制见 [R2 记录](./docs/记录/2026-10-07-多目录多会话工作区完善.md)。
