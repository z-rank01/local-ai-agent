# 本地 AI Agent 部署指南

> 面向"刚 clone 下来、想尽快跑起来"的用户。本文只讲**装依赖、配置、启动、验证、排查**。
> 架构设计、内部实现、技能开发、提示词调优不在本文范围，见 [`docs/`](./docs/README.md) 与各模块源码。

---

## 0. 快速开始

日常统一入口为 `scripts/start-daily.ps1`，每日操作见[日常运行指南](docs/日常运行指南.md)。下方 `quick-start.ps1` 和 IN1 为保留启动方式；安装完成后日常使用统一入口。

Windows PowerShell：

```powershell
git clone <repo-url> local-ai-agent
cd local-ai-agent

# 1) 准备配置
Copy-Item .env.example .env
# 编辑 .env，至少确认 OLLAMA_MODEL 与 `ollama list` 里的名字一致

# 2) 安装 Python 依赖
python -m pip install -r requirements.txt

# 3) 一键启动（交互式询问是否启用联网搜索）
.\scripts\quick-start.ps1
```

脚本会自动：检查环境 → 启动容器 → 启动 Python BFF → 安装并启动 Web UI → 打开浏览器。

停止：

```powershell
.\scripts\stop-backend.ps1        # 只停 BFF
docker compose down               # 停容器
```

> 首次启动会构建容器镜像，耗时取决于网络。代码有改动需重建镜像时加 `-Build`。

---

## 1. 前置要求

| 软件 | 版本 | 用途 | 检查命令 |
| --- | --- | --- | --- |
| **Docker Desktop** | 含 Compose v2 | 运行 skill 微服务容器 | `docker --version` / `docker compose version` |
| **Ollama** | 任意近期版本 | 本地模型推理 | `ollama list` |
| **Python** | **≥ 3.11** | BFF 与 TUI | `python --version` |
| **Node.js** | ≥ 18（含 npm） | Web UI / Ink CLI | `node --version` / `npm --version` |
| **Git** | 任意 | 版本控制；容器内 workspace 也用 | `git --version` |

一键自检（逐项 PASS/FAIL，缺项时退出码 1）：

```powershell
.\scripts\check-env.ps1        # Linux/macOS: ./scripts/check-env.sh
```

**Ollama 模型**：`.env` 的 `OLLAMA_MODEL` 必须与 `ollama list` 完全一致，否则模型不可用。

```powershell
ollama pull qwen3.5:27b        # 推荐，工具调用能力较强，需 24GB+ 显存
# 显存有限（8GB 左右）：qwen2.5:7b 或 qwen2.5-coder:7b
```

---

## 2. 首次部署步骤

### 2.1 获取代码

```powershell
git clone <repo-url> local-ai-agent
cd local-ai-agent
```

### 2.2 准备 `.env`

```powershell
Copy-Item .env.example .env
```

`.env` 已被 `.gitignore` 忽略，不会提交。逐项说明见 [§6](#6-配置参考env)。通常只需确认：

- `OLLAMA_MODEL` 与本地已拉取的模型名一致
- `ENABLE_WEBSEARCH` 保持 `false`（不需要联网搜索时）

### 2.3 安装 Python 依赖

```powershell
python -m pip install -r requirements.txt
```

或按 `pyproject.toml` 可编辑安装（会注册 `local-ai-agent` 与 `local-ai-agent-api` 两个命令）：

```powershell
python -m pip install -e .
```

### 2.4 启动

```powershell
.\scripts\quick-start.ps1
```

Linux / macOS：

```bash
./scripts/quick-start.sh
```

脚本按顺序完成：

1. 停掉可能残留的 BFF 进程（仅限 `BFF_PORT`）
2. 初始化 `data/` 目录结构与 workspace Git 仓库
3. 检查 Docker Desktop、Ollama、Python、Node.js
4. **交互式询问**是否启用联网搜索（Y/N，默认 N）——结果写回 `.env`
5. `docker compose up -d`（启用搜索时自动加 `--profile websearch`）
6. 等待容器健康检查通过（默认最长 120 秒）
7. 启动 Python BFF（`python -m bff`，后台隐藏窗口，日志写入文件）
8. 按需安装 Web 依赖（`apps/web/node_modules` 缺失时自动 `npm install`）
9. 启动 Vite 开发服务器并打开浏览器

> **Web UI 以 Vite 开发模式运行**（`npm run dev`），不是生产构建。需要生产构建请执行 `npm run build --prefix apps/web`。

### 2.5 访问

启动成功后终端会打印实际地址。默认：

| 服务 | 默认地址 |
| --- | --- |
| **Web 聊天界面** | http://127.0.0.1:5173 |
| BFF API | http://127.0.0.1:9510 |
| skill-files | http://127.0.0.1:9101 |
| skill-runner | http://127.0.0.1:9102 |
| skill-websearch（可选） | http://127.0.0.1:9103 |

Web 端口被占用时脚本会自动换用可用端口并提示；可用 `.env` 的 `WEB_PORT` 指定首选值。

### 2.6 验证

```powershell
docker compose ps                          # 容器状态（基础 2 个，启用搜索 4 个）

Invoke-RestMethod http://localhost:9101/health     # skill-files
Invoke-RestMethod http://localhost:9102/health     # skill-runner
Invoke-RestMethod http://127.0.0.1:9510/api/status # BFF
```

健康端点返回 `{"status":"ok"}` 即正常。随后在浏览器打开 Web 界面，在**顶部"模型设置"**选择模型（如用云端模型则填写密钥），发一条消息确认能正常流式回复。

### 2.7 停止

```powershell
.\scripts\stop-backend.ps1        # 仅停 BFF，Docker 与 Ollama 不动
docker compose down              # 停容器（数据 volume 保留）
docker compose --profile websearch down   # 含搜索服务
```

---

## 3. 保留启动方式与 IN1 隔离环境

除日常统一入口外，以下两种启动方式继续保留。它们与日常入口共用 Web/BFF 端口，不能同时运行。

| | **路径 A：原通用入口** | **路径 B：IN1 隔离环境** |
| --- | --- | --- |
| 入口 | `.\scripts\quick-start.ps1` | `.\scripts\start-in1.ps1` |
| Compose 文件 | `docker-compose.yml` | `compose.in1.yml` |
| 容器端口 | 9101 / 9102 / 9103 | **19101 / 19102 / 19103** |
| BFF 启动方式 | `python -m bff` | `scripts/run_in1.py` |
| BFF 端口 | `BFF_PORT`（默认 9510） | **固定 9510** |
| Web 端口 | 5173（自动避让） | **固定 5173** |
| 工作区 | `data/workspace` | **`data/in1/workspace`** |
| 会话库 | `data/conversations.db` | **`data/in1/conversations.db`** |
| Python 解释器 | PATH 上的 `python` | **必须是 `.conda/python.exe`** |
| 用途 | 日常聊天与工具 | 隔离环境下的接入验证、验收 |

**为什么路径 B 要额外准备 Python 环境**：`start-in1.ps1` 硬编码使用仓库内的 `.conda/python.exe`，而 **`.conda/` 不在版本控制中**——刚 clone 下来并不存在。缺失时脚本直接报错：

```
Install the project Python environment first; see 本地AI-Agents部署流程.md.
```

准备方式（任意 Python 3.11+）：

```powershell
python -m venv .conda
.\.conda\python.exe -m pip install -e .
npm ci --prefix apps/web
```

之后再运行：

```powershell
.\scripts\start-in1.ps1              # 或加 -Build 重建镜像
```

成功输出：`IN1 Web: http://127.0.0.1:5173 (model settings are at the top)`

**若 9510 被另一个工作区占用**，`start-in1.ps1` 会明确拒绝启动而不是静默复用——先停掉那个 BFF。

### 路径 B 会覆盖 `.env`（重要）

`scripts/run_in1.py` 在导入应用**之前**把这些变量写入进程环境，而 `core/config.py` 加载 `.env` 时**不覆盖已存在的环境变量**。因此在路径 B 下，**改 `.env` 的以下项无效**：

| 变量 | 路径 B 强制值 |
| --- | --- |
| `WORKSPACE_PATH` | `data/in1/workspace` |
| `DB_PATH` | `data/in1/conversations.db` |
| `LOG_PATH` | `data/in1/logs/audit.jsonl` |
| `SKILL_FILES_URL` / `SKILL_RUNNER_URL` / `SKILL_WEBSEARCH_URL` | `127.0.0.1:19101 / 19102 / 19103` |
| `WORKSPACE_CLOUD_ALLOWED` | `true` |
| `DEFAULT_MODEL` | `qwen:qwen3.5-flash` |
| `MODEL_CALL_LIMIT` | `120` |
| `ENABLE_WEBSEARCH` | `true` |
| `TOOL_TIER` | `all` |

需要改这些值时，用路径 A，或直接编辑 `scripts/run_in1.py`。

---

### 3.1 IN1 隔离环境维护

在 Web 顶部选择模型，在“模型设置”保存密钥与授权。密钥不回显；隔离工作区默认授权不覆盖已保存的用户选择。

服务代码更新后，工具容器用 `docker compose -f compose.in1.yml restart` 重启；BFF 代码更新需退出后重跑 `scripts/start-in1.ps1`。页面“关闭 Python 后端”只停止 BFF；“退出前端与后端”用于退出整套 Web 服务，股票任务需先收尾。停止隔离工具容器使用 `docker compose -f compose.in1.yml stop`。这些操作不删除隔离会话库或工作区。

## 4. `quick-start.ps1` 启动参数

| 参数 | 作用 |
| --- | --- |
| `-Build` | 启动容器前重建镜像（代码或 `Dockerfile` 有改动时用） |
| `-SkipFrontend` | 只起后端（容器 + BFF），不启动 Web / CLI |
| `-LaunchCLI` | 启动 legacy Ink CLI，而不是 Web UI |
| `-StopBackend` | 只停掉占用 `BFF_PORT` 的 BFF 进程后退出 |
| `-KeepOpen` | 保持脚本前台运行，按 `q` 回车停掉本次启动的 BFF 与 Web |

```powershell
.\scripts\quick-start.ps1 -Build          # 重建镜像后启动
.\scripts\quick-start.ps1 -SkipFrontend   # 只要后端
.\scripts\quick-start.ps1 -StopBackend    # 只停 BFF
```

Shell 版对应开关：`SKIP_FRONTEND=1`、`LAUNCH_CLI=1`、`--stop-backend`、`--keep-open`。

Web 前端连接的后端地址可用 `apps/web/.env.local` 覆盖：

```dotenv
VITE_LOCAL_AI_AGENT_API_URL=http://127.0.0.1:9510
```

允许访问 BFF 的 origin 可用根目录 `.env` 的 `WEB_ORIGINS` 指定（逗号分隔）。

---

## 5. 联网搜索（可选）

需要**两层同时打开**：

1. **`.env`**：`ENABLE_WEBSEARCH=true`
2. **容器 profile**：`docker compose --profile websearch up -d`

`quick-start.ps1` 的交互提问会同时处理这两件事并把结果写回 `.env`。手动启用：

```powershell
# 编辑 .env 设置 ENABLE_WEBSEARCH=true
docker compose --profile websearch up -d
```

启用的服务：`searxng`（元搜索引擎，仅容器内暴露 :8080）与 `skill-websearch`（工具后端 :9103）。

| 变量 | 默认 | 说明 |
| --- | --- | --- |
| `ENABLE_WEBSEARCH` | `false` | 总开关；关闭时不注册联网工具 |
| `WEB_SEARCH_BUDGET` | 见 `.env.example` | 每轮用户消息的搜索次数上限 |
| `WEB_FETCH_BUDGET` | 见 `.env.example` | 每轮网页抓取次数上限 |
| `FETCH_TIMEOUT` | 30 | 抓取超时（秒） |
| `MAX_FETCH_CHARS` | 20000 | 单页抓取字符上限 |

SearXNG 配置位于 `config/searxng/`；处于代理网络下时按需配置容器代理。

> 额度**每轮重新计算**：模型在一轮内超出预算时，工具会返回剩余次数提示；额度用尽后本轮不再允许联网。

---

## 6. 配置参考（`.env`）

自 `.env.example` 复制，按用途分组：

| 变量 | 默认 | 说明 |
| --- | --- | --- |
| `SKILL_FILES_PORT` | 9101 | skill-files 容器映射端口 |
| `SKILL_RUNNER_PORT` | 9102 | skill-runner 容器映射端口 |
| `SKILL_WEBSEARCH_PORT` | 9103 | skill-websearch 容器映射端口 |
| `OLLAMA_BASE_URL` | `http://localhost:11434` | Ollama 地址 |
| `OLLAMA_MODEL` | `qwen3.5:27b` | 必须与 `ollama list` 一致 |
| `LOG_LEVEL` | `INFO` | 日志级别 |
| `BFF_HOST` | `127.0.0.1` | BFF 监听地址 |
| `BFF_PORT` | 9510 | BFF 监听端口 |
| `PYTHON_EXEC_TIMEOUT` | 120 | 容器内 Python 执行超时（秒） |
| `SHELL_EXEC_TIMEOUT` | 60 | 容器内 shell 执行超时（秒） |
| `AUTO_GIT_COMMIT` | `true` | workspace 内文件变更是否自动提交 |
| `ENABLE_WEBSEARCH` | `false` | 联网搜索总开关 |
| `WORKSPACE_CLOUD_ALLOWED` | `false` | **云端模型是否可用本地工作区工具**；关闭时本地文件内容不随工具结果发往云端 |
| `TOOL_TIER` | `all` | 工具分层：`core` 只暴露核心工具，`all` 全部 |
| `DEFAULT_MODEL` | 未设 | 默认模型标识（路径 A 使用） |
| `MODEL_CATALOG` | `config/models.json` | 模型目录文件 |
| `MODEL_CALL_LIMIT` | 未设 | 持久化模型调用次数预算（含失败尝试），达到后停止调用 |
| `STOCK_BRIDGE_URL` / `STOCK_BRIDGE_TOKEN` | 空 | 股票只读桥接；**留空时相关工具完全不注册** |

> **密钥不要写进 `.env` 或文档**。云端供应商密钥请在 Web 界面顶部"模型设置"中填写，由本机保存。`.env` 与 `data/` 均已被 `.gitignore` 忽略。

---

## 7. 常用运维命令

```powershell
# 容器日志
docker compose logs -f                    # 全部
docker compose logs -f skill-runner       # 代码执行与技能管理
docker compose logs -f skill-files        # 文件读写
docker compose --profile websearch logs -f skill-websearch

# 应用日志
Get-Content data\logs\web-ui.log -Tail 50      # Web UI
Get-Content data\logs\web-ui.err.log -Tail 50
Get-Content data\logs\bff.log -Tail 50         # BFF（quick-start 启动时）
Get-Content data\logs\bff.err.log -Tail 50
Get-Content data\logs\audit.jsonl -Tail 20     # 工具调用审计

# 重建
docker compose up -d --build              # 重建全部核心服务
docker compose build --no-cache           # 遇到缓存异常问题时

# 停止
docker compose down                       # 保留数据 volume
docker compose down -v                    # 连 volume 一起删（会丢失容器内 pip 包）

docker compose ps                         # 状态
```

**数据落盘位置**（删除前请确认）：

| 路径 | 内容 |
| --- | --- |
| `data/workspace/` | 用户工作区（AI 可读写），自身是一个独立 Git 仓库 |
| `data/conversations.db` | 会话与消息（含工具调用记录） |
| `data/logs/` | BFF / Web 日志与 `audit.jsonl` 审计日志 |
| `data/trash/` | 文件软删除回收站 |
| `data/in1/` | 路径 B 的隔离工作区、会话库与日志 |

---

## 8. 故障排查

| 症状 | 可能原因 | 处理 |
| --- | --- | --- |
| `docker compose up` 失败 | Docker Desktop 未启动 | 启动 Docker Desktop，等状态变为 Running |
| 容器反复重启 | 端口被占用 | 改 `.env` 里的 `SKILL_*_PORT`，或释放占用端口 |
| 模型不可用 / 无回复 | `OLLAMA_MODEL` 与本地模型名不一致 | `ollama list` 核对后修正 `.env` |
| 页面能开但发消息报错 | BFF 未就绪或端口不符 | `Invoke-RestMethod http://127.0.0.1:9510/api/status`；`.\scripts\stop-backend.ps1` 后重启 |
| 提示 9510 被另一个工作区占用 | 路径 A 与路径 B 冲突 | 两者不能同时运行，先停掉其中一个 |
| `start-in1.ps1` 报缺 Python 环境 | `.conda/` 未创建（不在版本控制中） | 见 §3 创建 `.conda` 并 `pip install -e .` |
| 改了 `.env` 但路径 B 不生效 | `run_in1.py` 硬编码覆盖 | 见 §3 覆盖表；改用路径 A 或改脚本 |
| Web UI 起不来 | 前端依赖缺失或端口冲突 | 看 `data/logs/web-ui.err.log`；`npm install --prefix apps/web` |
| 联网搜索无结果 | 只开了 `.env` 没开 profile | 用 `--profile websearch` 启动容器 |
| 工具调用报容器错误 | 容器未健康 | `docker compose ps` 看健康状态，`logs -f` 定位 |
| 想彻底重来 | 配置或数据残留 | 停服务 → 删 `data/` → 重新跑 `quick-start.ps1` |

---

## 9. 架构与目录（简述）

```
┌─────────────────────────────────────────────────────────┐
│  宿主机                                                  │
│   Web UI (Vite :5173)  ──►  BFF (FastAPI :9510)         │
│                                  │                       │
│                             core/ 运行时（agent loop）   │
│                                  │ HTTP                  │
│  ┌───────────────────────────────┼─────────────────────┐ │
│  │ Docker: agent-net             ▼                     │ │
│  │   skill-files     :9101   文件读写、软删除、Git       │ │
│  │   skill-runner    :9102   Python/shell 执行、技能注册 │ │
│  │   skill-websearch :9103   联网搜索（可选 profile）    │ │
│  │   searxng         :8080   元搜索引擎（仅容器内）       │ │
│  └─────────────────────────────────────────────────────┘ │
│                                  │                       │
│                             Ollama :11434               │
└─────────────────────────────────────────────────────────┘
```

容器侧隔离：只读根文件系统、`tmpfs /tmp`、非 root 用户、`cap_drop: ALL`、`no-new-privileges`、CPU/内存限制、超时后杀整个进程组。所有文件路径经 `PathGuard` 限制在 `/workspace` 内。

主要目录：

```
local-ai-agent/
├── apps/web/          React + Vite 聊天界面（主入口）
├── apps/cli-ink/      legacy Ink 终端界面
├── bff/               FastAPI 前端适配层
├── core/              agent 运行时：工具注册/路由、上下文、记忆、策略、审计
├── tui/               Textual 终端界面（legacy）
├── gateway/           旧 gateway 代码（已废弃，仅作参考）
├── skills/            skill 微服务（files / runner / websearch），各含 Dockerfile
├── config/
│   ├── tools/*.yaml   工具定义（每个文件一个工具）
│   ├── policy.yaml    路径白名单/黑名单、执行限制
│   ├── models.json    模型目录
│   └── searxng/       搜索引擎配置
├── scripts/           部署、检查与启停脚本
├── tests/             离线测试
├── data/              运行时数据（已 gitignore）
├── docker-compose.yml     路径 A 编排
└── compose.in1.yml        路径 B 编排
```

---

## 10. 相关文档

| 文档 | 内容 |
| --- | --- |
| [`docs/README.md`](./docs/README.md) | 文档入口与当前工程状态 |
| [`docs/历史/开发与验收记录-2026-09.md`](./docs/历史/开发与验收记录-2026-09.md) | 历史实现、环境与验收证据 |
| [`docs/回归验证指南.md`](./docs/回归验证指南.md) | 聊天、工具与股票服务回归方法 |
| [`docs/股票能力接入.md`](./docs/股票能力接入.md) | 股票能力接入的分阶段计划 |
| [`docs/harness能力盘点.md`](./docs/harness能力盘点.md) | 本仓库作为 agent harness 的能力盘点 |


## 11. 日常统一入口

首次安装完成后，运行 `scripts/start-daily.ps1` 打开 Web。在右侧“股票服务”配置仓库与状态目录，开启/暂停任务执行，处理恢复摘要。脚本不强制启动 Ollama，使用既有模型配置；配置和开关选择本地保存。旧 IN1 环境继续保留，两个启动模式不能同时占用 9510。每日操作见 [日常运行指南](docs/日常运行指南.md)。
