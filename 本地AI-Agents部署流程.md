# 本地 AI Agent 部署与使用文档

> 适用环境：Windows + Docker Desktop + Ollama（本机运行）+ Python 3.11+ + Node.js LTS + Ink CLI 终端界面
>
> 最终架构：Ink CLI（本地 Node.js 进程）→ Python BFF（localhost:9510）→ core/ → Ollama（localhost:11434）+ skill-files（:9101）+ skill-runner（:9102）+ SearXNG + skill-websearch（:9103，可选）
>
> 对话存储：SQLite（data/conversations.db）

---

## 一、日常使用流程

> 每次使用前按以下顺序确认服务状态。

### 1. 确认 Ollama 正在运行

Ollama 在 Windows 上通常以托盘程序方式运行，开机自启。确认方式：

```powershell
ollama list
```

正常输出应包含 `gemma4:26b`（或你实际使用的模型名，以 `.env` 中 `OLLAMA_MODEL` 为准）。若不在，先拉取：

```powershell
ollama pull gemma4:26b
```

### 2. 启动容器服务

在项目根目录执行：

```powershell
cd C:\Users\Administrator\local-ai-agent

# 基础启动（2 个核心 skill 服务）
docker compose up -d

# 如需联网搜索功能（4 个服务）
docker compose --profile websearch up -d
```

> 也可使用一键启动脚本 `scripts/quick-start.ps1`，会自动检查 Python、Node.js、Docker、Ollama，启动 skill 服务、Python BFF，并启动 Ink CLI。

### 3. 确认容器健康

```powershell
docker compose ps
```

预期输出（基础模式 2 个服务，启用搜索时 4 个服务）：

```
NAME              STATUS
skill-files       Up X seconds (healthy)
skill-runner      Up X seconds (healthy)
searxng           Up X seconds (healthy)      # 仅启用搜索时
skill-websearch   Up X seconds (healthy)      # 仅启用搜索时
```

可手动验证 skill 服务健康：

```powershell
# 验证 skill-files（端口 9101）
Invoke-RestMethod http://localhost:9101/health

# 验证 skill-runner（端口 9102）
Invoke-RestMethod http://localhost:9102/health
```

返回 `{"status":"ok"}` 即正常。

### 4. 启动 Ink CLI 界面

```powershell
cd C:\Users\Administrator\local-ai-agent
python -m bff

# 另开一个终端
cd C:\Users\Administrator\local-ai-agent\apps\cli-ink
npm run dev
```

Ink CLI 会在终端中启动新的命令行界面；其前端通过本机的 Python BFF 调用现有 core/ 能力，而不是直接嵌入 Python UI 框架。

> 也可直接运行 `scripts/quick-start.ps1`，脚本会在启动所有服务后自动启动 Ink CLI。若只想启动后端链路，可使用 `-SkipCLI`。

### 5. 在 Ink CLI 中对话

- 在提示框中输入消息，按 Enter 发送
- 当前最小原型已经支持连接 Python BFF、展示流式 assistant 输出、thinking 日志块和 tool 日志块
- 若消息里包含 Windows 本地绝对路径，例如 `C:\Users\Administrator\Desktop\report.pptx`，后端会自动导入到 `data/workspace/data/` 并改写成 `/workspace/data/report.pptx`
- `scripts/quick-start.ps1 -SkipCLI` 可只启动 skill 服务和 BFF，便于单独调试前端

### 6. 停止服务

```powershell
docker compose down
```

### 7. 查看审计日志

所有工具调用均记录在：

```
data/logs/audit.jsonl
```

每行一条 JSON，字段包括时间戳、工具名、参数。

---

## 二、系统架构说明

```
Ink CLI (终端界面, Node.js + Ink)
  │  通过 HTTP 调用前端适配层
  ├── Python BFF (localhost:9510)
  │   ├── bff/app.py             ← 前端协议层
  │   ├── bff/service.py         ← 会话 / 工作区 / 流式事件规范化
  │   └── core/runtime.py        ← 共享后端装配
  │
  ├── core/agent.py              ← Agentic loop（最多6轮工具调用）
  │   ├── core/llm_client         → Ollama (localhost:11434)
  │   ├── core/tool_router        → skill services (HTTP)
  │   ├── core/policy_engine      策略检查
  │   └── core/audit_logger       审计日志
  │
  ├── core/conversation_store     → SQLite (data/conversations.db)
  ├── core/memory_manager         → MEMORY.md + 对话记忆
  │
  └── Docker skill services (沙箱)
      ├── skill-files      :9101  文件 I/O
      ├── skill-runner     :9102  代码执行 + 技能管理
      └── skill-websearch  :9103 (可选) 联网搜索
        │
        └── SearXNG :8080 (内部)
```

- **Ink CLI**：基于 Node.js + Ink 的终端前端，负责终端交互、流式转录区、输入区以及后续的抽屉/弹窗体验。
- **Python BFF**：新的前端适配层，负责会话 CRUD、流式聊天、事件规范化、工作区浏览和本地路径导入，不让前端直接碰 `core/` 内部对象或技能服务。
- **core/**：保留为纯 Python 业务内核，包含 Agent（agentic loop）、LLM 客户端、工具路由、策略引擎、审计日志、记忆管理、上下文管理、Prompt 构建等模块，由 BFF 统一装配和调用。
- **SQLite 对话存储**：替代 Open WebUI 的对话管理，支持对话列表、消息历史、搜索。WAL 模式确保并发安全。
- **skill-files**：文件 I/O 工具服务，所有路径通过 `PathGuard` 强制限制在 `/workspace` 下，支持读写、列目录、软删除（trash）、重命名、git 提交。内置 Excel（pandas）和 PDF（pymupdf）快速解析。端口 9101 对宿主机暴露，TUI 直连。
- **skill-runner**：代码执行沙箱 + 技能管理中心 + 文件转换器插件宿主，提供 `code_exec`、`shell_exec`、`pip_install`、技能 CRUD 管理、`file_convert`。容器级安全加固：`read_only` 文件系统、`cap_drop: ALL`、`mem_limit: 2048m`、`cpus: 2.0`。端口 9102 对宿主机暴露。
- **skill-websearch**（可选）：联网搜索工具服务，封装 SearXNG API。通过 `ENABLE_WEBSEARCH` 环境变量和 Docker Compose `profiles: websearch` 控制。端口 9103 对宿主机暴露。
- **SearXNG**（可选）：自托管元搜索引擎，聚合多个搜索引擎结果（Bing、DuckDuckGo、Wikipedia 等），无需 API key。
- **Ollama**：在宿主机本地运行，TUI 通过 `localhost:11434` 直连。

---

## 三、项目目录结构

```
local-ai-agent/
├── .env                        # 端口、模型名、功能开关、执行超时
├── .gitignore
├── docker-compose.yml
├── requirements.txt            # TUI + core 顶层依赖
├── pyproject.toml              # pip install -e . + python -m tui
├── config/
│   ├── policy.yaml             # 路径白名单/黑名单、write-only 保护、代码执行黑名单
│   ├── runtime.yaml            # 运行时参数
│   ├── git.yaml                # workspace git 配置
│   ├── searxng/                # ← SearXNG 搜索引擎配置
│   │   └── settings.yml        #    搜索引擎列表、API 模式、安全设置
│   └── tools/                  # ← YAML 工具注册表（每个 .yaml 对应一个工具）
│       ├── file_read.yaml
│       ├── file_write.yaml
│       ├── code_exec.yaml
│       ├── skill_run.yaml
│       ├── skill_register.yaml # ← 技能 CRUD 工具
│       ├── web_search.yaml     # ← 联网搜索工具
│       └── ...（共 19 个）
├── core/                       # ← 核心业务逻辑包（TUI 直接 import）
│   ├── __init__.py
│   ├── config.py               # 集中配置管理
│   ├── agent.py                # Agentic loop（Agent 类 + AgentEvent）
│   ├── llm_client.py           # Ollama HTTP 客户端
│   ├── tool_registry.py        # YAML 工具注册表加载
│   ├── tool_router.py          # 工具名 → skill 服务路由
│   ├── policy_engine.py        # 策略检查
│   ├── context_manager.py      # 上下文压缩
│   ├── audit_logger.py         # JSONL 审计日志
│   ├── prompt_builder.py       # 系统提示词构建
│   ├── memory_manager.py       # 统一记忆管理（MEMORY.md + 对话记忆）
│   └── conversation_store.py   # SQLite 对话存储
├── tui/                        # ← Textual TUI 界面
│   ├── __init__.py
│   ├── __main__.py             # python -m tui 入口
│   ├── app.py                  # AgentApp 主应用
│   ├── utils.py                # 辅助函数
│   ├── screens/
│   │   └── chat_screen.py      # 主聊天界面 Screen
│   ├── widgets/
│   │   ├── conversation_list.py # 左侧栏：对话列表
│   │   ├── chat_view.py        # 主区域：消息流
│   │   ├── message_widget.py   # 消息组件（含思考/工具折叠）
│   │   ├── file_explorer.py    # 底部：workspace 文件树
│   │   └── input_bar.py        # 输入栏
│   └── styles/
│       └── app.tcss            # Textual CSS 样式
├── gateway/                    # ← 旧 gateway 代码（已废弃，保留作参考）
│   ├── app.py                  # 原 FastAPI 入口
│   ├── llm_client.py
│   ├── tool_registry.py
│   ├── tool_router.py
│   ├── policy_engine.py
│   ├── audit_logger.py
│   └── prompts/
│       └── system.txt          # 系统提示词
│       └── modules/            # 模块化提示词
├── skills/
│   ├── files/                  # skill-files 服务（Docker 容器）
│   │   ├── Dockerfile
│   │   ├── app.py
│   │   ├── path_guard.py       # 防目录遍历
│   │   ├── file_ops.py         # 文件读写（编码检测、Excel/PDF 解析）
│   │   ├── trash.py            # 软删除
│   │   └── git_ops.py          # GitPython 封装
│   ├── runner/                 # skill-runner 服务（Docker 容器）
│   │   ├── Dockerfile          # 非 root（uid 1001 runner）
│   │   ├── app.py              # code_exec/shell_exec/skill CRUD
│   │   ├── sandbox.py          # subprocess 沙箱执行器
│   │   ├── skill_registry.py   # 技能 CRUD + 注册持久化
│   │   └── converter_registry.py # 转换器插件注册表
│   └── websearch/              # ← 联网搜索服务（可选）
│       ├── Dockerfile
│       └── app.py              # web_search + web_fetch
├── data/
│   ├── conversations.db        # ← SQLite 对话数据库
│   ├── workspace/              # Agent 可操作的文件区（已 git init）
│   │   ├── data/               # 用户数据
│   │   ├── docs/               # 文档
│   │   ├── reports/            # AI 生成的报告
│   │   ├── skills/             # 动态工具目录
│   │   ├── converters/         # 转换器插件目录
│   │   └── .memory/            # 记忆存储（MEMORY.md + 对话记忆）
│   ├── trash/                  # 软删除暂存区
│   └── logs/
│       └── audit.jsonl         # 审计日志
└── scripts/
    ├── quick-start.ps1         # 一键启动脚本
    ├── quick-start.sh
    ├── check-env.ps1           # 环境检查脚本
    ├── check-env.sh
    ├── init-workspace.ps1      # workspace 初始化
    └── init-workspace.sh
```

---

## 四、从零部署步骤

### 前置要求

| 组件 | 说明 |
|---|---|
| Windows + Docker Desktop | 提供 Docker daemon、Compose、容器网络 |
| Git | `data/workspace` 版本控制，宿主机也需要 |
| Ollama | 宿主机本地运行，拉取好目标模型 |
| Python 3.11+ | 运行 TUI 和 core 模块所需 |
| Textual + httpx | `pip install -r requirements.txt` |

检查命令：

```powershell
docker --version
docker compose version
git --version
ollama list
python --version
```

---

### Step 1：创建项目骨架目录

```powershell
mkdir -p C:\Users\Administrator\local-ai-agent
cd C:\Users\Administrator\local-ai-agent

mkdir config\tools, config\searxng,
      core, tui\widgets, tui\screens, tui\styles,
      gateway\prompts\modules,
      skills\files, skills\runner, skills\websearch,
      data\workspace\data, data\workspace\skills,
      data\trash, data\logs, scripts
```

---

### Step 2：初始化 workspace Git 仓库

skill-files 的 `git_commit` 工具依赖 `data/workspace` 已是 git 仓库。

```powershell
cd data\workspace

git init -b main
git config user.name "Local AI Agent"
git config user.email "local-agent@example.local"

# 创建目录结构（data/ 放用户数据，skills/ 放 AI 创建的工具脚本）
New-Item data\.gitkeep -ItemType File
New-Item skills\.gitkeep -ItemType File
New-Item .gitignore -ItemType File

git add .
git commit -m "chore: initialize workspace"
```

`.gitignore` 最小内容（防止私钥、临时文件入仓）：

```gitignore
*.tmp
*.swp
*.bak
.DS_Store
.env
*.pem
*.key
id_rsa
id_ed25519
```

---

### Step 3：创建配置文件

**`.env`**（项目根目录）：

```dotenv
# ── Skill Service Ports ────────────────────────────────────────────────────
SKILL_FILES_PORT=9101
SKILL_RUNNER_PORT=9102
SKILL_WEBSEARCH_PORT=9103

# ── Ollama ─────────────────────────────────────────────────────────────────
OLLAMA_BASE_URL=http://localhost:11434
OLLAMA_MODEL=gemma4:26b

# ── Paths ──────────────────────────────────────────────────────────────────
WORKSPACE_PATH=./data/workspace
DB_PATH=./data/conversations.db

# ── Logging ────────────────────────────────────────────────────────────────
LOG_LEVEL=INFO
PYTHON_EXEC_TIMEOUT=120
SHELL_EXEC_TIMEOUT=60

# ── Feature flags ──────────────────────────────────────────────────────────
AUTO_GIT_COMMIT=true
ENABLE_WEBSEARCH=false
```

> **坑（Windows）**：Windows 可能将 8000–8390 端口段保留给 Hyper-V / WinNAT，导致 Docker 绑定时报 `An attempt was made to access a socket in a way forbidden by its access permissions`。
> 排查方法：`netsh int ipv4 show excludedportrange`。默认端口 9101-9103 已选在安全范围外。

**`config/policy.yaml`** — 定义路径白名单、write-only 保护（防止 AI 删除工具脚本）、代码执行黑名单。

**`config/git.yaml`** — workspace git 参数（用户名、邮箱、自动提交开关）。

---

### Step 4：编写服务代码

核心文件清单（按优先级）：

```
# skill-files 服务（文件 I/O）
skills/files/Dockerfile
skills/files/requirements.txt
skills/files/path_guard.py    ← 最先写，所有路径都靠它校验
skills/files/file_ops.py
skills/files/trash.py
skills/files/git_ops.py
skills/files/app.py

# skill-runner 服务（代码执行 + 动态工具）
skills/runner/Dockerfile      ← 非 root 用户（uid 1001 runner）
skills/runner/requirements.txt
skills/runner/sandbox.py      ← subprocess 沙箱，超时+资源限制
skills/runner/skill_registry.py ← 扫描 /workspace/skills/ 动态加载
skills/runner/app.py          ← code_exec/shell_exec/skill_list/skill_run

# core 业务逻辑包（TUI 直接 import）
core/config.py
core/llm_client.py
core/policy_engine.py
core/audit_logger.py
core/tool_registry.py          ← 扫描 config/tools/*.yaml 生成工具定义
core/tool_router.py
core/agent.py                  ← Agentic loop
core/memory_manager.py
core/conversation_store.py     ← SQLite 对话存储
core/prompt_builder.py

# TUI 界面
tui/app.py
tui/screens/chat_screen.py
tui/widgets/*.py

# 工具 YAML 注册表（每个工具一个文件）
config/tools/file_read.yaml
config/tools/file_write.yaml
config/tools/file_list.yaml
config/tools/file_delete.yaml
config/tools/file_rename.yaml
config/tools/git_status.yaml
config/tools/git_commit.yaml
config/tools/code_exec.yaml
config/tools/shell_exec.yaml
config/tools/skill_list.yaml
config/tools/skill_run.yaml
```

**skill-files Dockerfile 关键内容**：

```dockerfile
FROM python:3.11-slim

# 只保留 trixie 主源 + security 源
# 去掉 trixie-updates（该端点在部分网络/代理下不稳定，会返回 502）
RUN printf 'deb http://deb.debian.org/debian trixie main\ndeb http://deb.debian.org/debian-security trixie-security main\n' \
        > /etc/apt/sources.list \
    && apt-get update \
    && apt-get install -y --no-install-recommends git curl \
    && rm -rf /var/lib/apt/lists/*
```

> **坑**：不手动覆盖 `sources.list` 的话，默认包含的 `trixie-updates` 在部分代理/网络下会返回 502 或连接 EOF，导致 `apt-get update` 失败，整个构建报错。

**skill-runner Dockerfile 关键内容**：

```dockerfile
FROM python:3.11-slim
# 非 root 用户，uid 1001
RUN adduser --uid 1001 --disabled-password --gecos "" runner
USER runner
```

> skill-runner 需要 `curl`（healthcheck 用），但其容器设置了 `cap_drop: ALL`，因此 curl 只能在 apt 阶段作为 root 安装，运行时以 runner 用户执行。

> **注意**：gateway 已不再作为 Docker 服务运行，其逻辑已迁移至 `core/` 包，由 TUI 直接调用。`gateway/` 目录仅保留 `prompts/` 子目录供系统提示词模块使用。

---

### Step 5：编写 docker-compose.yml

关键配置点：

```yaml
services:
  skill-runner:
    # 安全加固：只读文件系统、内存限制、cap_drop
    read_only: true
    tmpfs:
      - /tmp
    cap_drop: [ALL]
    mem_limit: 2048m
    cpus: 2.0
    security_opt: ["no-new-privileges:true"]
    ports:
      - "${SKILL_RUNNER_PORT:-9102}:8200"  # 对宿主机暴露，TUI 直连

  skill-files:
    ports:
      - "${SKILL_FILES_PORT:-9101}:8100"   # 对宿主机暴露，TUI 直连

  # ── 以下为可选的联网搜索服务（通过 profiles 控制） ──

  searxng:
    image: searxng/searxng:latest
    profiles: [websearch]
    volumes:
      - ./config/searxng:/etc/searxng  # 不能加 :ro，SearXNG 启动时需要 chown
    expose: ["8080"]

  skill-websearch:
    profiles: [websearch]
    ports:
      - "${SKILL_WEBSEARCH_PORT:-9103}:8300"
    depends_on:
      searxng:
        condition: service_healthy

networks:
  agent-net:
    driver: bridge
```

> **注意**：`agent-gateway` 服务已移除。TUI 直接通过 `localhost` 连接各 skill 服务，无需中间 HTTP 层。

---

### Step 6：首次构建

```powershell
docker compose build
```

> **坑：Docker Hub 网络问题（国内环境）**
>
> `python:3.11-slim` 镜像从 Docker Hub 拉取，`apt-get` 依赖包从 `deb.debian.org` 拉取。在国内网络下：
>
> - 如需代理，在 Docker Desktop → Settings → Resources → Proxies 配置代理地址。
> - 同时在 No Proxy 字段加入 `deb.debian.org,localhost,127.0.0.1`，防止 apt 流量也走代理（代理配错会导致 apt 返回 407 或连接中断）。
> - 开启 VPN 全局模式可以绕过大部分连接问题。

> **坑：BuildKit 缓存损坏**
>
> 若出现类似 `failed to solve: DeadlineExceeded` 或缓存层拉不到的报错，用以下命令清理重建：
>
> ```powershell
> docker builder prune -af
> docker compose build --no-cache
> ```
>
> `prune -af` 会清空所有 BuildKit 缓存，彻底解决缓存损坏问题，但下次构建会完整重新下载。

---

### Step 7：首次启动与验证

**基础启动（2 个核心 skill 服务）：**

```powershell
docker compose up -d
docker compose ps
```

**启用联网搜索（4 个服务）：**

```powershell
# 需先在 .env 中设置 ENABLE_WEBSEARCH=true
docker compose --profile websearch up -d
docker compose ps
```

等约 45 秒后，所有服务均应显示 `healthy`。

验证 skill 服务连通性：

```powershell
# 1. 验证 skill-files 健康（端口 9101）
Invoke-RestMethod http://localhost:9101/health

# 2. 验证 skill-runner 健康（端口 9102）
Invoke-RestMethod http://localhost:9102/health
```

### Step 8：安装 Python 依赖 & 启动 TUI

```powershell
cd C:\Users\Administrator\local-ai-agent

# 安装依赖
pip install -r requirements.txt

# 或以开发模式安装（推荐）
pip install -e .

# 启动 TUI
python -m tui
```

TUI 启动后会在终端中显示多面板界面，可直接开始对话。

---

## 五、系统提示词调优

系统提示词位于 `gateway/prompts/` 目录，由 `core/prompt_builder.py` 在运行时动态加载。模块化文件存放在 `gateway/prompts/modules/` 下，修改后立即生效（无需重启）。

**关键规则（必须写明，否则模型经常不遵守）**：

1. **语言规则**：明确写 "回复语言要与用户输入语言一致"，否则 qwen2.5:7b 可能默认回英文。
2. **强制工具调用**：写清楚"当用户要求操作文件时，**必须立即调用对应工具**，不能解释怎么做，不能让用户自己去跑命令"。
3. **工具列表**：把所有可用工具及其参数逐一列出，否则模型不知道有哪些工具可以用。工具列表由 `config/tools/*.yaml` 驱动，新增工具时同步更新系统提示词。
4. **路径规则**：写明"所有文件操作路径必须以 `/workspace` 开头；用户数据存 `/workspace/data/`；创建新工具脚本存 `/workspace/skills/`"。
5. **执行后摘要**：要求模型在工具调用完成后用结构化格式输出操作摘要（✅ 执行步骤 / 📊 结果 / 💡 说明），否则模型有时只返回空消息。
6. **自主决策**：写明 "当用户说 '任意/随便/你来决定' 时，直接做出合理选择并执行，不要反问"。
7. **禁止输出原始 JSON 工具调用**：明确写 "永远不要在文字回复里输出 `{"name": "...", "arguments": {...}}` 格式的原始 JSON；工具调用通过 function-call 机制静默完成，回复里只用 inline code 引用工具名"。

---

## 六、常用运维命令

```powershell
# 查看实时日志（所有 skill 服务）
docker compose logs -f

# 只看 skill-runner 日志（代码执行、技能 CRUD 操作）
docker compose logs -f skill-runner

# 只看 skill-files 日志
docker compose logs -f skill-files

# 只看搜索相关日志（需要已启用 websearch profile）
docker compose logs -f skill-websearch searxng

# 重建单个服务（代码有改动时）
docker compose build skill-runner
docker compose up -d --no-deps skill-runner

# 重建全部核心服务
docker compose build
docker compose up -d

# 重建全部（含搜索服务）
docker compose --profile websearch build
docker compose --profile websearch up -d

# 强制不用缓存重建（遇到奇怪的缓存问题时）
docker compose build --no-cache

# 停止并清除容器（数据 volume 保留）
docker compose down
# 停止包含搜索服务的容器
docker compose --profile websearch down

# 查看 workspace git 日志（进容器）
docker exec -it skill-files git -C /workspace log --oneline -10

# 启动 TUI
python -m tui
```

---

## 七、可用工具一览

所有工具定义存放在 `config/tools/*.yaml`，`core/tool_registry.py` 启动时自动加载。添加新工具只需新建 YAML 文件并重启 TUI。当前共 **19 个工具**。

### 文件操作工具（skill-files 后端）

| 工具 | 必填参数 | 说明 |
|---|---|---|
| `file_write` | `path`, `content` | 创建或覆盖文件，自动 git commit |
| `file_read` | `path` | 读取文件内容（自动检测编码，内置 Excel/PDF 解析，未知二进制自动链式调用 file_convert） |
| `file_convert` | `path` | 将非文本文件转换为纯文本（通过 `/workspace/converters/` 插件），通常由 gateway 自动链式调用 |
| `file_list` | `directory` | 列出目录内容 |
| `file_rename` | `src`, `dst` | 重命名或移动文件 |
| `file_delete` | `path` | 软删除（移入 `/trash`，可恢复）；`/workspace/skills/` 下的文件禁止删除 |
| `git_status` | 无 | 查看 workspace 当前 git 状态 |
| `git_commit` | `message` | 手动提交所有变更 |

### 执行工具（skill-runner 后端，沙箱隔离）

| 工具 | 必填参数 | 说明 |
|---|---|---|
| `code_exec` | `code` | 在沙箱内执行 Python 代码片段，临时文件写入 /tmp，执行后立即删除 |
| `shell_exec` | `command` | 在 /workspace 执行 shell 命令，适合目录浏览等轻量操作 |
| `pip_install` | `package` | 安装 Python 依赖到 `/packages` 共享目录 |
| `skill_list` | 无 | 列出 `/workspace/skills/` 下所有已注册动态工具及其参数 |
| `skill_run` | `skill_name` | 按名称执行已注册的动态工具（.py 文件中的 `run()` 函数） |

### 技能 CRUD 管理工具（skill-runner 后端）

| 工具 | 必填参数 | 可选参数 | 说明 |
|---|---|---|---|
| `skill_register` | `skill_name`, `code` | `auto_install_deps` | 注册新技能：验证格式（SKILL_METADATA + run 函数）、写入 .py 文件、保存配置到注册表、自动安装依赖 |
| `skill_unregister` | `skill_name` | — | 删除技能：移除 .py 文件和注册配置 |
| `skill_info` | `skill_name` | — | 查看技能详细信息（描述、参数、依赖、版本、运行统计等） |
| `skill_update` | `skill_name` | `code`, `metadata` | 更新技能代码或配置元数据 |

### 联网搜索工具（skill-websearch 后端，可选）

| 工具 | 必填参数 | 可选参数 | 说明 |
|---|---|---|---|
| `web_search` | `query` | `max_results`（默认 5） | 通过 SearXNG 搜索引擎搜索，返回标题 + 链接 + 摘要。**预算限制：每轮最多 3 次** |
| `web_fetch` | `url` | `max_chars`（默认 5000） | 抓取网页 URL 并用 readability 提取正文内容。**预算限制：每轮最多 2 次** |

> **工具调用预算**：为防止模型反复搜索导致上下文溢出，`core/agent.py` 对 `web_search` 和 `web_fetch` 设有硬性调用次数上限。达到上限后工具定义会从候选列表中移除，强制模型用已有结果回答。

**路径规则**：
- 所有文件路径必须以 `/workspace` 开头，超出范围的请求会被 `PathGuard` 拦截返回 403
- 用户数据存 `/workspace/data/`，动态工具脚本存 `/workspace/skills/`
- `/workspace/skills/` 下的文件只能写入/更新，不能删除或重命名移出

---

## 八、踩坑汇总

| 问题 | 现象 | 解决方案 |
|---|---|---|
| Docker Hub 连接超时 | `pull` 卡住或报 i/o timeout | 开 VPN 全局模式，Docker Desktop 配置代理 |
| apt-get 502 / EOF | `trixie-updates` 源构建失败 | Dockerfile 手动覆盖 `sources.list`，只保留 `trixie` 和 `trixie-security` |
| BuildKit 缓存损坏 | 构建 DeadlineExceeded 或拉缓存层失败 | `docker builder prune -af` 后 `--no-cache` 重建 |
| 模型回英文，不调用工具 | 用中文对话但模型回英文，且只解释操作步骤而不实际执行 | 系统提示词加语言规则 + 强制工具调用说明 + 工具列表 |
| 代码改了但容器行为没变 | 构建走了缓存跳过 COPY 层 | 确认用 `docker compose build`（而非只执行 `up`），必要时加 `--no-cache` |
| `.env` 模型名与 Ollama 不一致 | 调用 Ollama 报 404 或模型不存在 | 用 `ollama list` 确认模型名，`.env` 里 `OLLAMA_MODEL` 完全对应 |
| Windows 端口被 Hyper-V 保留 | Docker 绑定端口报 `access permissions` 错误 | 用 `netsh int ipv4 show excludedportrange protocol=tcp` 查看保留范围；端口已改为 9101-9103 避开保留段 |
| 模型在文字回复里输出原始 JSON 工具调用 | 回复中出现 `{"name": "file_list", "arguments": {...}}` 的 JSON 代码块 | 系统提示词加禁止规则：工具调用通过 function-call 机制静默完成，回复里只用 inline code 引用工具名 |
| SearXNG 启动失败（unhealthy） | `container searxng is unhealthy`，日志报 `chown` 权限错误 | SearXNG 配置卷 `./config/searxng:/etc/searxng` **不能加 `:ro`** |
| SearXNG 端口不对 | 连接 8888 超时，搜索无结果返回 | SearXNG 官方 Docker 镜像监听 **8080** 端口，health check 和内部引用都要用 8080 |
| `lxml_html_clean` 缺失 | skill-websearch 启动后 `web_fetch` 报 `ImportError: lxml.html.clean` | `requirements.txt` 中显式添加 `lxml_html_clean` |
| AI 反复搜索不回答 | 搜索 5+ 轮后上下文溢出 | `core/agent.py` 内置硬性工具调用预算（`web_search: 2`, `web_fetch: 2`），超额后从候选列表移除 |
| 读取 GBK 编码文件报 500 | `file_read` 读取中文文本文件报 `UnicodeDecodeError` | `file_ops.py` 改用编码回退链：utf-8 → gbk → gb2312 → gb18030 → big5 → latin-1 |
| AI 读 PDF 输出乱码 | 用 `file_read` 读取 PDF 文件返回二进制原始数据 | `file_ops.py` 增加 `pymupdf` 内置 PDF 解析 |
| AI 使用中文但回复英文 | 模型受英文 prompt 影响回英文 | 将 `system.txt` 改写为中文，开头加"语言规则（最高优先级）" |
| 新文件格式需重建容器 | 每遇到新格式都需修改代码 + 重建镜像 | 转换器插件系统：`/workspace/converters/` 下放 Python 插件，热插拔 |

---

## 九、动态工具创建指南（AI 自建工具）

AI 可以在运行时创建和管理自定义 Python 工具，有两种方式：通过 **CRUD 工具注册**（推荐）或 **直接写文件**。

### 工具脚本格式

每个工具是一个独立的 `.py` 文件，必须包含 `SKILL_METADATA` 字典和 `run()` 函数：

```python
# /workspace/skills/my_tool.py

SKILL_METADATA = {
    "name": "my_tool",                  # 必填：技能名（与文件名一致）
    "description": "工具的简短描述",      # 必填：功能描述
    "version": "1.0.0",                 # 可选：版本号
    "author": "user",                   # 可选：作者
    "dependencies": ["requests"],       # 可选：pip 依赖列表（注册时自动安装）
    "parameters": {                     # 必填：参数定义（OpenAI function schema）
        "type": "object",
        "properties": {
            "param_name": {
                "type": "string",
                "description": "参数说明"
            }
        },
        "required": ["param_name"]
    }
}

def run(params: dict) -> dict:
    """技能入口函数，接收参数字典，返回结果字典。"""
    value = params.get("param_name", "")
    # ... 处理逻辑 ...
    return {"result": value}
```

### 方式一：通过 CRUD 工具注册（推荐）

使用 `skill_register` 工具注册技能，自动完成格式验证、配置持久化、依赖安装：

1. AI 调用 `skill_register` 传入技能名和代码 → 自动验证 `SKILL_METADATA` + `run()` 格式，安装依赖，写入 `.py` 文件和注册配置
2. AI 调用 `skill_run` 执行技能
3. 如需修改：AI 调用 `skill_update` 更新代码或元数据
4. 如需删除：AI 调用 `skill_unregister` 移除技能和配置

**管理命令：**
- `skill_list` — 列出所有已注册技能
- `skill_info` — 查看单个技能的详细信息（描述、参数、依赖、运行统计）
- `skill_update` — 更新技能代码或配置
- `skill_unregister` — 删除技能

**注册配置持久化：**

技能注册信息存储在 `/workspace/.skill_registry/` 目录下（持久化卷）：

```
/workspace/.skill_registry/
├── registry.json          # 所有技能的索引（名称、路径、状态、创建时间等）
└── skills/
    ├── my_tool.json       # 每个技能的独立配置（参数、依赖、运行统计等）
    └── ...
```

### 方式二：直接写入文件（旧方式，仍支持）

1. AI 调用 `file_write` 将工具脚本写入 `/workspace/skills/<name>.py`
2. AI 调用 `skill_list` 确认新工具已被发现
3. AI 调用 `skill_run` 执行新工具（`{"skill_name": "<name>", "params": {...}}`）

> 注意：直接写入的技能不会有注册配置，无法通过 `skill_info` 查看详情。建议使用 CRUD 工具注册方式。

### 依赖管理

- 技能依赖统一安装到共享的 `/packages` 目录（容器内 volume），所有技能共享
- 不需要为每个技能创建独立的虚拟环境（Docker 容器已提供隔离）
- `skill_register` 自动读取 `SKILL_METADATA["dependencies"]` 并调用 `pip install`
- 手动安装依赖：通过 `pip_install` 工具安装

### 工具保护机制

`/workspace/skills/` 是 **write-only** 目录（由 `config/policy.yaml` 的 `write_only_prefixes` 控制）：
- ✅ 允许：`file_write` 创建或更新工具脚本
- ✅ 允许：`skill_register` / `skill_update` / `skill_unregister` 管理技能
- ❌ 禁止：`file_delete` 删除工具脚本（返回 403）
- ❌ 禁止：`file_rename` 将工具脚本移出该目录（返回 403）

如需手动删除某个工具，直接在宿主机操作 `data\workspace\skills\` 目录即可。

---

## 十、添加静态工具（YAML 注册表）

静态工具（如 `file_read`、`code_exec`）由开发者通过 YAML 文件注册，无需修改 Python 代码。

### 注册新工具

在 `config/tools/` 下新建一个 YAML 文件：

```yaml
# config/tools/my_new_tool.yaml
name: my_new_tool
backend: skill-runner        # 或 skill-files / skill-websearch
description: "工具的功能描述（会显示给 AI 模型）"
parameters:
  type: object
  properties:
    input:
      type: string
      description: "输入参数说明"
  required: ["input"]
```

然后在对应的后端服务（`skills/runner/app.py` 或 `skills/files/app.py`）添加对应路由，重新启动 TUI 即可加载新工具：

```powershell
# 重启 TUI 以加载新工具定义
python -m tui
```

### 架构设计说明

工具注册表采用 **双层架构**，两层有不同的信任级别：

| 层 | 存放位置 | 谁来写 | 信任级别 | 执行方式 |
|---|---|---|---|---|
| 静态工具（Layer 1） | `config/tools/*.yaml` + 对应服务代码 | 开发者，经 git 审核 | 完全可信 | 直接 HTTP 调用 |
| 动态工具（Layer 2） | `/workspace/skills/*.py` | AI 在运行时创建 | 沙箱受限 | subprocess 隔离执行 |

两层**不可合并**：合并会让 AI 写的代码获得与开发者代码同等权限，绕过沙箱限制。

---

## 十一、技能体系架构

系统采用 **三层技能** 架构，各司其职：

| 层级 | 类型 | 存放位置 | 管理方式 | 执行环境 |
|---|---|---|---|---|
| **Layer 1：固有技能** | 系统内置工具 | `config/tools/*.yaml` + 后端代码 | 开发者维护，git 审核 | 直接 HTTP 路由到对应微服务 |
| **Layer 2：新增技能** | 用户注册的 Python 脚本 | `/workspace/skills/*.py` + `.skill_registry/` | AI 通过 CRUD 工具管理 | subprocess 沙箱隔离执行 |
| **Layer 3：临时技能** | 一次性代码片段 | 无持久化，写入 /tmp 执行后删除 | AI 通过 `code_exec` / `shell_exec` | subprocess 沙箱隔离执行 |

### Layer 1：固有技能（Built-in）

系统核心能力，始终可用：

- **文件操作**：`file_read`, `file_write`, `file_list`, `file_delete`, `file_rename`
- **文件转换**：`file_convert`（通过 `/workspace/converters/` 转换器插件将非文本文件转为纯文本）
- **Git 操作**：`git_status`, `git_commit`
- **代码执行**：`code_exec`, `shell_exec`, `pip_install`
- **技能管理**：`skill_list`, `skill_run`, `skill_register`, `skill_unregister`, `skill_info`, `skill_update`
- **联网搜索**：`web_search`, `web_fetch`（可选，需启用 websearch profile）

固有技能由 YAML 文件定义（`config/tools/*.yaml`），路由到三个后端微服务之一（`skill-files` / `skill-runner` / `skill-websearch`）。

### Layer 2：新增技能（Registered）

用户通过 CRUD 工具注册的自定义 Python 脚本：

- 有标准格式（`SKILL_METADATA` + `run()` 函数）
- 支持依赖声明和自动安装
- 配置持久化到 `/workspace/.skill_registry/`
- 通过 `skill_run` 以 subprocess 方式在沙箱内执行

### Layer 3：临时技能（Temporary）

AI 通过 `code_exec` 或 `shell_exec` 直接执行的一次性代码：

- 无需注册，适合临时计算、数据转换等一次性操作
- 代码写入 `/tmp`，执行后立即删除
- 同样在 subprocess 沙箱内执行，有超时限制

### 虚拟环境策略

**不需要为每个技能创建独立的 Python 虚拟环境**。理由：

1. 所有技能已在 Docker 容器内运行，天然进程隔离
2. 每个技能执行通过 subprocess 隔离
3. `/packages` 卷提供共享包存储（所有技能共用）
4. 创建 per-skill venv 会增加磁盘占用和管理复杂度

---

## 十二、联网搜索（Web Search）

### 架构

```
用户提问 → AI 判断是否需要搜索 → web_search（调 SearXNG）→ 获取摘要
                                  → web_fetch（可选，抓取详细页面）→ 提取正文
                                  → AI 综合信息回答
```

组件：
- **SearXNG**：自托管元搜索引擎（Docker 镜像 `searxng/searxng:latest`），聚合 Bing、DuckDuckGo、Wikipedia 等搜索引擎，无需 API key
- **skill-websearch**：FastAPI 微服务，封装 SearXNG JSON API 和网页正文提取

### 部署方式

1. 在 `.env` 中设置 `ENABLE_WEBSEARCH=true`
2. 使用 `--profile websearch` 启动：

```powershell
docker compose --profile websearch build
docker compose --profile websearch up -d
```

或使用 `scripts/quick-start.ps1` 脚本（会交互式询问是否启用搜索）。

### 功能开关（双层）

| 层 | 机制 | 说明 |
|---|---|---|
| Docker 层 | `profiles: [websearch]` | 不加 `--profile websearch` 则 SearXNG 和 skill-websearch 容器不会创建 |
| Gateway 层 | `ENABLE_WEBSEARCH` 环境变量 | 为 `false` 时即使容器存在也不加载搜索工具 |

不部署搜索功能时，其他功能完全不受影响。

### SearXNG 配置

配置文件 `config/searxng/settings.yml` 关键项：

```yaml
use_default_settings: true
server:
  bind_address: "0.0.0.0"
  port: 8080                  # SearXNG Docker 镜像固定监听 8080
  limiter: false              # 内网使用，关闭 rate limit
search:
  formats: ["json"]           # 仅 API 模式，不需要 Web UI
engines:
  - name: bing
    disabled: false
  - name: duckduckgo
    disabled: false
  - name: wikipedia
    disabled: false
  - name: google
    disabled: true            # Google 直连可能被封，列为可选
```

> **注意**：SearXNG 挂载卷 `./config/searxng:/etc/searxng` **不能加 `:ro`**，启动时需要 `chown` 修改目录权限。

### 工具调用预算

为防止模型反复搜索导致上下文溢出（尤其是 27B 以下模型），gateway 内置硬性调用预算：

| 工具 | 单轮上限 | 超额行为 |
|---|---|---|
| `web_search` | 3 次 | 从候选工具列表中移除，强制模型用已有结果回答 |
| `web_fetch` | 2 次 | 同上 |

预算在每次用户消息请求时重置。实现位于 `core/agent.py` 的 `_TOOL_BUDGETS` 和 agentic loop 中。

### 代理配置（可选）

如需通过代理访问外部搜索引擎，在 `docker-compose.yml` 的 `searxng` 服务中添加环境变量：

```yaml
environment:
  - HTTP_PROXY=http://proxy:port
  - HTTPS_PROXY=http://proxy:port
```

---

## 十三、TUI 界面输出结构

TUI 界面（Textual）通过 `core/agent.py` 的 `Agent.run()` async generator 接收结构化事件流（`AgentEvent`），在终端中实时渲染：

### 1. 思维链（Thinking）

- 渲染为 Collapsible 折叠组件，标题 "💭 思考过程"
- 默认折叠，用户可点击展开查看 AI 的完整推理过程
- 来源：`AgentEvent(kind="token")` 中的 thinking 字段

### 2. 工具调用状态

- 渲染为 Collapsible 折叠组件，标题 "🔧 tool_name → 参数摘要 — ✅/❌"
- 默认折叠，展开可查看完整参数和返回结果
- 执行期间显示 spinner + 工具名
- 完成后更新状态图标为 ✅（成功）或 ❌（失败）
- 来源：`AgentEvent(kind="tool_start")` 和 `AgentEvent(kind="tool_end")`

### 3. 回答内容

- Markdown 渲染（通过 Textual 的 Markdown 组件 + Rich 库）
- 支持标题、列表、表格、代码块、数学公式
- 流式逐 token 追加，实时更新显示
- 来源：`AgentEvent(kind="token")` 中的 content 字段

### 4. 用户消息

- 带 `>` 前缀标记
- 自动检测并高亮文件路径和 URL
- 拖入文件路径会渲染为 `【filename.ext】` 视觉框

### 布局

```
┌──────────────────────────────────────────────────────┐
│  Local AI Agent v2.0                                 │
├────────────┬─────────────────────────────────────────┤
│ 对话列表    │  聊天消息区（滚动）                      │
│            │  > 用户消息                              │
│ □ 对话1    │  💭 思考过程 (折叠)                      │
│ ■ 对话2    │  🔧 file_read → ... — ✅ (折叠)         │
│ □ 对话3    │  AI 回复内容 (Markdown)                  │
│            │                                         │
│            ├─────────────────────────────────────────┤
│            │  输入消息... (拖入文件或粘贴 URL)          │
│            ├─────────────────────────────────────────┤
│            │  📁 workspace 文件浏览器 (Ctrl+E 切换)    │
└────────────┴─────────────────────────────────────────┘
```

---

## 十四、文件读取与转换器插件系统

### 问题背景

原始 `file_read` 仅支持纯文本文件（UTF-8），遇到以下场景会失败：

1. **编码问题**：GBK 编码的中文文件读取报 `UnicodeDecodeError`（HTTP 500）
2. **PDF 文件**：直接读取二进制原始数据返回乱码，AI 无法理解内容
3. **二进制文档**：`.docx`、`.pptx` 等格式无法读取，AI 只能告诉用户"这是一个 XXX 文件"
4. **扩展性差**：每增加一种新文件格式，都需要修改 Python 代码 + 重新构建 Docker 镜像

### 三层文件读取架构

```
AI 调用 file_read(path)
        │
        ▼
┌─ Layer 1：skill-files 内置快速路径 ─────────────────────┐
│  • 文本文件（.txt/.csv/.py 等 ~40 种）→ 自动编码检测      │
│  • Excel（.xlsx/.xls）→ pandas 解析为 CSV 文本           │
│  • PDF（.pdf）→ pymupdf 逐页提取文本                     │
│  • 未知二进制 → 返回 {"unsupported": true, ...}          │
└──────────────────────────────────────────────────────────┘
        │ unsupported
        ▼
┌─ Layer 2：gateway 自动链式调用 file_convert ─────────────┐
│  • gateway tool_router 检测到 unsupported 标记            │
│  • 透明转发给 skill-runner 的 file_convert 端点           │
│  • converter_registry 扫描 /workspace/converters/ 插件    │
│  • 自动安装插件依赖（pip）→ subprocess 隔离执行转换       │
│  • 返回纯文本结果，对 AI 完全透明                         │
└──────────────────────────────────────────────────────────┘
        │ 无匹配转换器
        ▼
┌─ Layer 3：AI 兜底（code_exec）──────────────────────────┐
│  • AI 使用 code_exec 编写临时代码读取文件                 │
│  • 成功后 AI 可创建持久转换器到 /workspace/converters/    │
│  • 下次遇到同类型文件自动走 Layer 2                       │
└──────────────────────────────────────────────────────────┘
```

**核心设计原则**：AI 只需调用 `file_read`，三层处理对其完全透明。

### Layer 1：编码检测与内置解析（skill-files）

**文件**：`skills/files/file_ops.py`

#### 编码自动回退

```python
_FALLBACK_ENCODINGS = ("utf-8", "gbk", "gb2312", "gb18030", "big5", "latin-1")

# 读取时依次尝试每种编码，最后用 errors="replace" 兜底
for enc in encodings:
    try:
        return raw.decode(enc)
    except (UnicodeDecodeError, LookupError):
        continue
return raw.decode("utf-8", errors="replace")
```

#### 二进制文件检测

```python
def _is_binary(data: bytes, sample_size: int = 8192) -> bool:
    sample = data[:sample_size]
    if b"\x00" in sample:
        return True
    control = sum(1 for b in sample if b < 8 or (14 <= b < 32))
    return control / len(sample) > 0.10
```

- `_TEXT_SUFFIXES` 集合定义 ~40 种已知文本扩展名，匹配时跳过二进制检测
- 非文本扩展名 + 二进制检测阳性 → 返回结构化 `unsupported` 响应

#### 内置格式支持

| 格式 | 依赖 | 处理方式 |
|---|---|---|
| 文本文件（~40 种扩展名） | 无 | 自动编码检测 + 回退链 |
| Excel（.xlsx/.xls/.xlsm/.xlsb） | pandas + openpyxl | 按 Sheet 解析为 CSV 文本 |
| PDF（.pdf） | pymupdf | 逐页提取文本，扫描件返回提示信息 |

### Layer 2：转换器插件系统（skill-runner）

**文件**：`skills/runner/converter_registry.py`、`skills/runner/app.py`

#### 自动链式调用机制

```
file_read 返回 {"unsupported": true, "extension": ".docx", "path": "...", ...}
    ↓
gateway tool_router.dispatch() 检测到 unsupported 标记
    ↓
自动调用 _dispatch_convert(path) → skill-runner POST /tool/file_convert
    ↓
converter_registry.convert_file(path) → 扫描插件 → 安装依赖 → 隔离执行
    ↓
返回 {"content": "提取的纯文本"} → AI 正常收到文件内容
```

关键代码在 `gateway/tool_router.py` 的 `dispatch()` 方法中：

```python
# Auto-chain: when file_read returns unsupported binary, try file_convert
if tool == "file_read" and isinstance(result, dict) and result.get("unsupported"):
    if "file_convert" in self._registry.known_tools:
        convert_path = result.get("path") or params.get("path")
        if convert_path:
            convert_result = await self._dispatch_convert(convert_path, session_id)
            if not convert_result.get("unsupported") and not convert_result.get("error"):
                return convert_result
```

#### 转换器插件接口

每个转换器是 `/workspace/converters/` 下的独立 Python 文件，必须包含：

```python
# /workspace/converters/xxx_converter.py

CONVERTER_META = {
    "extensions": [".xxx"],           # 支持的文件扩展名列表
    "dependencies": ["pip-package"],  # pip 依赖，首次使用时自动安装
    "description": "描述信息"
}

def convert(file_path: str) -> str:
    """将文件转换为纯文本。"""
    # ... 读取并转换
    return extracted_text
```

#### 预置转换器

| 转换器 | 扩展名 | pip 依赖 | 说明 |
|---|---|---|---|
| `docx_converter.py` | .docx | python-docx | 提取段落 + 表格文本 |
| `pptx_converter.py` | .pptx | python-pptx | 按幻灯片提取文本框 + 表格 |
| `html_converter.py` | .html, .htm | beautifulsoup4 | 从 HTML 提取纯文本（去标签） |

> **注意**：`.html`/`.htm` 同时在 `_TEXT_SUFFIXES` 中（Layer 1 直接读取原始 HTML），`html_converter` 用于从复杂 HTML 中提取干净文本的场景（需手动调 `file_convert`）。

#### 添加新转换器

只需将新的 `xxx_converter.py` 放入 `/workspace/converters/`，**无需重建容器、无需重启服务**：

```python
# /workspace/converters/epub_converter.py
CONVERTER_META = {
    "extensions": [".epub"],
    "dependencies": ["ebooklib", "beautifulsoup4"],
    "description": "EPUB 电子书文本提取"
}

def convert(file_path: str) -> str:
    import ebooklib
    from ebooklib import epub
    from bs4 import BeautifulSoup

    book = epub.read_epub(file_path)
    texts = []
    for item in book.get_items_of_type(ebooklib.ITEM_DOCUMENT):
        soup = BeautifulSoup(item.get_content(), 'html.parser')
        texts.append(soup.get_text(separator='\n', strip=True))
    return '\n\n'.join(texts)
```

转换器的依赖在首次使用时自动通过 `pip_install` 安装到 `/packages` 卷，后续调用直接复用。

#### 转换器执行安全

- 转换器在独立 subprocess 中执行，与 skill-runner 主进程隔离
- 环境变量最小化（`_SAFE_ENV`），无法访问容器内部敏感信息
- 执行超时 60 秒（可通过 `PYTHON_EXEC_TIMEOUT` 环境变量调整）
- 输出通过 JSON 解析，防止注入

### Layer 3：AI 兜底与自动学习

当 Layer 1 和 Layer 2 都无法处理某种文件格式时：

1. AI 收到 `{"unsupported": true, "hint": "无匹配转换器..."}` 响应
2. AI 使用 `code_exec` 编写临时代码尝试读取文件
3. 如果成功，AI 按照系统提示词指引，创建持久化转换器到 `/workspace/converters/`
4. 后续遇到同类型文件自动走 Layer 2 路径

这种 **"遇到 → 解决 → 沉淀"** 的模式让系统能力随使用不断增长。

### Gateway 预取机制（格式无关）

`gateway/app.py` 中的 `_prefetch_file_context()` 负责在 AI 回复前预取用户提及的文件：

- **触发条件**：用户消息中包含文件相关关键词（workspace、文件、文档等）或文件名模式（`\w+\.\w{2,5}`）
- **格式无关**：不维护扩展名白名单，依赖 `file_read` + 自动链式调用处理任何格式
- **优雅降级**：无法读取的文件（unsupported 且无转换器）静默跳过，不影响其他文件的预取

### 涉及的文件变更总结

| 文件 | 变更类型 | 说明 |
|---|---|---|
| `skills/files/file_ops.py` | 修改 | 编码回退链 + 二进制检测 + PDF 内置支持 + unsupported 结构化返回 |
| `skills/files/app.py` | 修改 | file_read 端点处理 dict 返回 + 通用异常捕获 |
| `skills/files/requirements.txt` | 修改 | 添加 `pymupdf==1.25.3` |
| `skills/runner/converter_registry.py` | **新增** | 转换器扫描、依赖管理、隔离执行 |
| `skills/runner/app.py` | 修改 | 新增 `POST /tool/file_convert` 端点 |
| `config/tools/file_convert.yaml` | **新增** | file_convert 工具定义 |
| `core/tool_router.py` | 修改 | dispatch() 中增加自动链式调用逻辑 |
| `gateway/prompts/system.txt` | 修改 | 全面改写为中文，增加转换器插件使用指引 |
| `data/workspace/converters/*.py` | **新增** | 3 个预置转换器（docx/pptx/html） |
