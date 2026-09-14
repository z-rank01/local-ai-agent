# 本地 AI Agent 部署指南

> 面向"刚 clone 下来、想尽快跑起来"的用户。本文只讲**装依赖、配置、启动、验证、排查**。
> 架构设计、内部实现、技能开发、提示词调优不在本文范围，见 [`docs/`](./docs/README.md) 与各模块源码。
> **本仓库只有一个用户入口**：根目录 `启动.bat`（或 `scripts/start-daily.ps1`）。日常启动、回归验收、人工体验全部走这同一个入口与同一份数据（`data/conversations.db`），不存在第二条启动路径。

---

## 0. 快速开始

双击仓库根目录的 **`启动.bat`**（或 PowerShell 运行 `.\scripts\start-daily.ps1`）。脚本自动完成：检查 Docker → 启动工具容器 → 构建缺失的 Web 资源 → 启动服务 → 打开浏览器（<http://127.0.0.1:9510>）。

- 服务**已在运行**时重复执行只会打开浏览器，不会重复启动。
- **退出**：点页面上的"退出"，或直接关闭页面——全部服务（含工具容器）会在约 15 秒内自动停止。
- 仅聊天、暂不使用文件/Python 工具时，可加 `-SkipTools`（不启动容器）。

首次安装（只需一次）：

```powershell
git clone <repo-url> local-ai-agent
cd local-ai-agent
Copy-Item .env.example .env          # 按需编辑；普通云端使用无需改动
python -m pip install -r requirements.txt
```

> `.conda/` 是推荐的项目内环境（Git 忽略）：`python -m venv .conda; .\.conda\python.exe -m pip install -e .`。没有它时启动脚本会提示。

---

## 1. 前置要求

| 软件 | 版本 | 用途 | 检查命令 |
| --- | --- | --- | --- |
| **Docker Desktop** | 含 Compose v2 | 运行 skill 微服务容器 | `docker --version` / `docker compose version` |
| **Python** | **≥ 3.11** | 聊天后端 | `python --version` |
| **Node.js** | ≥ 18（含 npm） | 构建 Web 界面（首次自动） | `node --version` / `npm --version` |
| **Git** | 任意 | 版本控制；容器内 workspace 也用 | `git --version` |

一键自检（逐项 PASS/FAIL，缺项时退出码 1）：

```powershell
.\scripts\check-env.ps1        # Linux/macOS: ./scripts/check-env.sh
```

**Ollama（仅本地模型需要）**：`OLLAMA_MODEL` 必须与 `ollama list` 完全一致，否则模型不可用。使用云端模型不要求 Ollama 在线；推荐模型 `qwen3.5:27b`（需 24GB+ 显存），低显存用 `qwen2.5:7b`。

---

## 2. 首次部署步骤

### 2.1 获取代码并准备 `.env`

```powershell
git clone <repo-url> local-ai-agent
cd local-ai-agent
Copy-Item .env.example .env
```

`.env` 已被 `.gitignore` 忽略。逐项说明见 [§5](#5-配置参考env)。通常只需确认 `OLLAMA_MODEL`；其余保持默认。

### 2.2 安装依赖

```powershell
python -m pip install -r requirements.txt
# 或（注册可编辑安装）：
python -m pip install -e .
```

### 2.3 启动

```powershell
.\scripts\start-daily.ps1        # 或双击 启动.bat
```

脚本按顺序完成：

1. 检查 Docker Desktop（未运行则自动拉起，最长等 120 秒）
2. `docker compose up -d`：skill-files / skill-runner（联网搜索容器由页面开关按需拉起）
3. `apps/web/dist` 缺失时自动 `npm ci && npm run build`（之后代码更新可手动 `npm run build --prefix apps/web` 或 `-RebuildWeb`）
4. 启动聊天后端（`scripts/run_daily.py`，BFF 同时托管 Web 界面，单端口 9510）
5. 按上次保存的开关恢复股票后台与联网技能
6. 打开浏览器

### 2.4 访问与验证

| 服务 | 默认地址 |
| --- | --- |
| **Web 聊天界面（唯一入口）** | http://127.0.0.1:9510 |
| BFF API（内部） | http://127.0.0.1:9510 |

```powershell
docker compose ps                                             # 容器状态
Invoke-RestMethod http://127.0.0.1:9101/health                # skill-files
Invoke-RestMethod http://127.0.0.1:9102/health                # skill-runner
Invoke-RestMethod http://127.0.0.1:9510/api/status            # 服务状态（含工具列表）
```

随后在浏览器顶部"模型设置"选择模型（云端模型先填密钥），发一条消息确认流式回复正常。

---

## 3. 启动参数（`scripts/start-daily.ps1`）

| 参数 | 作用 |
| --- | --- |
| `-Build` | 启动容器前重建镜像（容器代码有改动时用） |
| `-RebuildWeb` | 强制重新构建 Web 界面 |
| `-SkipTools` | 只起聊天服务，不起工具容器（文件/Python 工具不可用） |

重复运行脚本 = 只打开浏览器。

## 4. 技能开关（页面上操作）

| 技能 | 开关位置 | 说明 |
| --- | --- | --- |
| **联网搜索** | 右栏"技能"面板 | 开启时自动启动搜索容器（首次约几秒）并注册 `web_search`/`web_fetch`；关闭即停容器、注销工具。开关本地保存，重启沿用。 |
| **股票** | 右栏"股票技能"面板 | 开启=启动股票后台并注册股票工具；日常参数见[日常运行指南](docs/日常运行指南.md)。连接配置、任务执行与恢复在"高级管理"折叠区。 |

## 5. 配置参考（`.env`）

自 `.env.example` 复制，按用途分组：

| 变量 | 默认 | 说明 |
| --- | --- | --- |
| `SKILL_FILES_PORT` / `SKILL_RUNNER_PORT` / `SKILL_WEBSEARCH_PORT` | 9101 / 9102 / 9103 | 工具容器映射端口 |
| `OLLAMA_BASE_URL` | `http://localhost:11434` | Ollama 地址 |
| `OLLAMA_MODEL` | `qwen3.5:27b` | 必须与 `ollama list` 一致 |
| `BFF_HOST` / `BFF_PORT` | `127.0.0.1` / 9510 | 聊天后端；Web 界面由它同源托管 |
| `PYTHON_EXEC_TIMEOUT` / `SHELL_EXEC_TIMEOUT` | 120 / 60 | 容器内执行超时（秒） |
| `AUTO_GIT_COMMIT` | `true` | workspace 内文件变更是否自动提交 |
| `ENABLE_WEBSEARCH` | `false` | 联网搜索**初始默认值**；之后以页面开关的保存值为准 |
| `WORKSPACE_CLOUD_ALLOWED` | `false` | 云端模型是否可用本地工作区工具（也可在页面按工作区授权） |
| `TOOL_TIER` | `all` | 工具分层：`core` 只暴露核心工具，`all` 全部 |
| `DEFAULT_MODEL` | 未设 | 默认模型标识 |
| `MODEL_CATALOG` | `config/models.json` | 模型目录文件 |
| `MODEL_CALL_LIMIT` | 未设 | 持久化模型调用次数预算（含失败尝试）；0/未设=不限，账本 `data/model-calls.sqlite` |
| `AUTO_EXIT_ON_CLOSE` | `true` | 关闭页面后自动停止全部服务；`false` 只保留"退出"按钮 |
| `HEARTBEAT_TIMEOUT_SECONDS` | `15` | 无心跳多少秒后自动退出 |
| `STOCK_BRIDGE_URL` / `STOCK_BRIDGE_TOKEN` | 空 | 股票桥接高级覆盖项：日常由页面连接配置管理，无需填写 |
| `WEB_SEARCH_BUDGET` / `WEB_FETCH_BUDGET` | 2 / 2 | 每轮用户消息的搜索/抓取次数上限 |

> **密钥不要写进 `.env` 或文档**。云端供应商密钥请在 Web 界面顶部"模型设置"中填写，由本机保存。`.env` 与 `data/` 均已被 `.gitignore` 忽略。

## 6. 停止

- **常规**：页面"退出"按钮，或直接关闭页面（约 15 秒后全部服务自动停止，股票任务先收尾）。
- **应急**（页面打不开时）：`.\scripts\stop-backend.ps1` 停聊天后端；`docker compose --profile websearch stop` 停容器。Docker Desktop 与 Ollama 是平台软件，按需自行退出。

再次使用：双击 `启动.bat`。

## 7. 常用运维命令

```powershell
docker compose logs -f                    # 容器日志
Get-Content data\logs\daily-bff.err.log -Tail 50     # 聊天后端错误
Get-Content data\logs\stock-service.err.log -Tail 50 # 股票后台错误
Get-Content data\logs\audit.jsonl -Tail 20           # 工具调用审计
docker compose up -d --build              # 重建并启动容器
```

**数据落盘位置**（删除前请确认）：

| 路径 | 内容 |
| --- | --- |
| `data/workspace/` | 用户工作区（AI 可读写），自身是一个独立 Git 仓库 |
| `data/conversations.db` | 会话与消息（含工具调用记录；历史隔离库已并入） |
| `data/model-calls.sqlite` | 模型调用预算账本 |
| `data/private/` | 页面保存的密钥/设置/技能开关（Git 忽略） |
| `data/logs/` | 服务日志与 `audit.jsonl` 审计 |
| `data/trash/` | 文件软删除回收站 |
| `data/_archive/` | 历史隔离与一次性测试数据（IN1 会话库已并入日常库） |

## 8. 故障排查

| 症状 | 处理 |
| --- | --- |
| `docker compose up` 失败 | 启动 Docker Desktop，等状态变为 Running |
| 容器反复重启 | 端口被占用：改 `.env` 的 `SKILL_*_PORT`，或释放占用端口 |
| 模型不可用 / 无回复 | `OLLAMA_MODEL` 与 `ollama list` 核对；云端模型检查"模型设置"里的密钥 |
| 页面能开但发消息报错 | `Invoke-RestMethod http://127.0.0.1:9510/api/status`；`stop-backend.ps1` 后双击启动 |
| 提示 9510 被占用 | 有旧后端在跑：从页面"退出"，或 `stop-backend.ps1` 后再启动 |
| 股票后台无法启动 | 检查页面"高级管理 → 连接配置"的路径/状态目录/端口与股票 Python 环境；看 `data/logs/stock-service.err.log` |
| 关闭页面后服务仍在 | 等待约 15 秒；仍不行则检查 `AUTO_EXIT_ON_CLOSE` 是否为 false |
| 想彻底重来 | 停止服务 → 备份后删 `data/` → 重新启动（首次配置重来） |

## 9. 架构与目录（简述）

```
┌──────────────────────────────────────────────────────────┐
│  宿主机                                                   │
│   浏览器 ──► BFF (FastAPI :9510) ──► core agent 运行时    │
│     （Web 界面由 BFF 同源托管，单端口单源）                 │
│                     │ HTTP                                 │
│  ┌──────────────────┼───────────────────────────────────┐ │
│  │ Docker: agent-net ▼                                  │ │
│  │   skill-files     :9101   文件读写、软删除、Git       │ │
│  │   skill-runner    :9102   Python/shell 执行、技能注册 │ │
│  │   skill-websearch :9103   联网搜索（页面开关拉起）    │ │
│  │   searxng         :8080   元搜索引擎（仅容器内）      │ │
│  └──────────────────────────────────────────────────────┘ │
│                     │                                     │
│                Ollama :11434（仅本地模型）                 │
└──────────────────────────────────────────────────────────┘
```

容器侧隔离：只读根文件系统、`tmpfs /tmp`、非 root 用户、`cap_drop: ALL`、`no-new-privileges`、CPU/内存限制、超时后杀整个进程组。所有文件路径经 `PathGuard` 限制在 `/workspace` 内。

```
local-ai-agent/
├── 启动.bat             唯一用户入口（双击启动）
├── apps/web/            React + Vite 聊天界面（生产构建由 BFF 托管）
├── bff/                 FastAPI 前端适配层（含服务/技能/退出管理）
├── core/                agent 运行时：工具注册/路由、上下文、记忆、策略、审计
│   ├── stock_service.py 股票后台进程管理
│   ├── skills_service.py 技能开关（联网搜索）
│   └── auto_exit.py     关页面自动退出判定
├── skills/              skill 微服务（files / runner / websearch），各含 Dockerfile
├── config/
│   ├── tools/*.yaml     工具定义（每个文件一个工具）
│   ├── prompts/         模块化提示词
│   ├── policy.yaml      路径白名单/黑名单、执行限制
│   └── models.json      模型目录
├── scripts/             启动/检查/迁移脚本（start-daily.ps1 为唯一入口）
├── tests/               离线测试
├── data/                运行时数据（已 gitignore）
├── docker-compose.yml   工具容器编排
└── 本地AI-Agents部署流程.md  本文
```

## 10. 相关文档

| 文档 | 内容 |
| --- | --- |
| [`docs/README.md`](./docs/README.md) | 文档入口与当前工程状态 |
| [`docs/日常运行指南.md`](./docs/日常运行指南.md) | 每天怎么用（技能开关、股票服务、退出） |
| [`docs/历史/开发与验收记录-2026-09.md`](./docs/历史/开发与验收记录-2026-09.md) | 历史实现、环境与验收证据 |
| [`docs/回归验证指南.md`](./docs/回归验证指南.md) | 聊天、工具与股票服务回归方法 |
| [`docs/股票能力接入.md`](./docs/股票能力接入.md) | 股票能力接入的分阶段计划 |
| [`AGENTS.md`](./AGENTS.md) | 给后续 AI 协作者的仓库规则（含唯一入口约定） |
