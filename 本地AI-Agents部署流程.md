# 本地 AI Agent 部署与使用文档

> 适用环境：Windows + Docker Desktop + Ollama（本机运行）+ Open WebUI
>
> 最终架构：Open WebUI → agent-gateway（:8400）→ Ollama（:11434）+ skill-files（内部:8100）+ skill-runner（内部:8200）+ SearXNG（内部:8080）+ skill-websearch（内部:8300）

---

## 一、日常使用流程

> 每次使用前按以下顺序确认服务状态。

### 1. 确认 Ollama 正在运行

Ollama 在 Windows 上通常以托盘程序方式运行，开机自启。确认方式：

```powershell
ollama list
```

正常输出应包含 `qwen3.5:27b`（或你实际使用的模型名，以 `.env` 中 `OLLAMA_MODEL` 为准）。若不在，先拉取：

```powershell
ollama pull qwen3.5:27b
```

### 2. 启动容器服务

在项目根目录执行：

```powershell
cd C:\Users\Administrator\local-ai-agent

# 基础启动（3 个核心服务）
docker compose up -d

# 如需联网搜索功能（5 个服务）
docker compose --profile websearch up -d
```

> 也可使用一键启动脚本 `scripts/quick-start.ps1`，会自动检查环境并交互式询问是否启用联网搜索。

### 3. 确认容器健康

```powershell
docker compose ps
```

预期输出（基础模式 3 个服务，启用搜索时 5 个服务）：

```
NAME              STATUS
skill-runner      Up X seconds (healthy)
skill-files       Up X seconds (healthy)
agent-gateway     Up X seconds (healthy)
searxng           Up X seconds (healthy)      # 仅启用搜索时
skill-websearch   Up X seconds (healthy)      # 仅启用搜索时
```

> **注意**：`agent-gateway` 依赖 `skill-files` 和 `skill-runner` 均健康才会启动。
> 首次启动等待约 45s 是正常的（healthcheck `start_period: 15s` + 首次 `interval: 30s`）。

也可以手动验证 gateway 端点：

```powershell
# 验证 agent-gateway（对外暴露 8400）
Invoke-RestMethod http://localhost:8400/health
```

返回 `{"status":"ok"}` 即正常。skill-files 和 skill-runner 均为内部服务，不对宿主机暴露端口。

### 4. 启动 Open WebUI

Open WebUI 以 Python 包方式运行（非 Docker）：

```powershell
open-webui serve --port 8888
```

默认监听 `http://localhost:8888`，浏览器打开即可。

> **坑（Windows）**：端口 8080 落在 Windows Hyper-V 保留的 `7991–8090` 段内，直接 `open-webui serve` 会报 `[Errno 13] access permissions`。
> 用 `--port` 指定 8390 以上的端口（推荐 8888 或 9000）即可绕过。

### 5. 在 Open WebUI 中选择 Agent 模型

打开 Open WebUI 后，在模型下拉框中选择 **`agent:qwen2.5:7b`**（而非直连的 `qwen2.5:7b`）。

> **说明**：`agent:qwen2.5:7b` 由 gateway 暴露，走完整的工具调用链路（文件读写、代码执行、动态工具、审计日志）。
> 直接选 `qwen2.5:7b` 会绕过 gateway，所有工具调用均不生效。

### 6. 停止服务

```powershell
docker compose down
```

### 7. 查看审计日志

所有通过 gateway 的工具调用均记录在：

```
data/logs/audit.jsonl
```

每行一条 JSON，字段包括时间戳、工具名、参数、session_id。

---

## 二、系统架构说明

```
Open WebUI (浏览器)
      │  OpenAI-compatible API
      ▼
agent-gateway  :8400          ← FastAPI，agentic loop（最多6轮）、策略检查、审计、工具预算
      │          │           │
      │ file I/O │ code/skill│  web search
      ▼          ▼           ▼
 skill-files  skill-runner  skill-websearch
 :8100（内部）  :8200（内部）  :8300（内部，可选）
 读写文件       code_exec      web_search
 git 操作       shell_exec     web_fetch
               skill CRUD         │
               skill_run          ▼
                            SearXNG :8080（内部，可选）
      │                     自托管元搜索引擎
      ▼                     聚合 Bing/DuckDuckGo/Wikipedia
   Ollama :11434
   (host 本机，通过 host.docker.internal 访问)
```

- **agent-gateway**：实现 OpenAI-compatible `/v1/chat/completions` + `/v1/models`，内部跑 agentic loop（最多 6 轮工具调用），通过 `ToolRegistry`（YAML 注册表）动态加载工具定义，策略检查由 `policy_engine.py` 执行。内置工具调用预算机制（如 `web_search` 最多 3 次），防止模型反复调用同一工具导致上下文溢出。流式回复通过 SSE 逐 token 发送，工具调用状态以 `<details>` 可折叠块注入流中。
- **skill-files**：文件 I/O 工具服务，所有路径通过 `PathGuard` 强制限制在 `/workspace` 下，支持读写、列目录、软删除（trash）、重命名、git 提交。仅在内部网络暴露，不对宿主机开放端口。
- **skill-runner**：代码执行沙箱 + 技能管理中心，提供临时代码执行（`code_exec`）、命令执行（`shell_exec`）、包安装（`pip_install`）、动态工具注册与 CRUD 管理（`skill_register` / `skill_unregister` / `skill_info` / `skill_update`）、技能执行（`skill_run`）。容器级安全加固：`read_only` 文件系统、`cap_drop: ALL`、`mem_limit: 2048m`、`cpus: 2.0`。
- **skill-websearch**（可选）：联网搜索工具服务，封装 SearXNG API 为 `web_search`（搜索）和 `web_fetch`（抓取网页正文）。通过 `ENABLE_WEBSEARCH` 环境变量和 Docker Compose `profiles: websearch` 控制是否启用。
- **SearXNG**（可选）：自托管元搜索引擎，聚合多个搜索引擎结果（Bing、DuckDuckGo、Wikipedia 等），无需 API key，完全免费。
- **Ollama**：在宿主机本地运行，容器内通过 `host.docker.internal:11434` 访问。
- **Open WebUI**：聊天界面，通过 OpenAI-compatible 接口连接 gateway（`http://localhost:8400/v1`）。

---

## 三、项目目录结构

```
local-ai-agent/
├── .env                        # 端口、模型名、功能开关、执行超时
├── .gitignore
├── docker-compose.yml
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
│       ├── skill_unregister.yaml
│       ├── skill_info.yaml
│       ├── skill_update.yaml
│       ├── web_search.yaml     # ← 联网搜索工具
│       ├── web_fetch.yaml
│       └── ...（共 18 个）
├── gateway/
│   ├── Dockerfile
│   ├── requirements.txt
│   ├── app.py                  # FastAPI 主入口，/v1/chat/completions，agentic loop，工具预算
│   ├── llm_client.py           # Ollama HTTP 客户端（chat / chat_raw / chat_stream）
│   ├── tool_registry.py        # ← 启动时扫描 config/tools/*.yaml，支持 enable_websearch 开关
│   ├── tool_router.py          # 工具名 → skill-files / skill-runner / skill-websearch 路由
│   ├── policy_engine.py        # 路径合法性检查、执行黑名单、write-only 保护
│   ├── audit_logger.py         # JSONL 审计日志（线程安全）
│   └── prompts/
│       └── system.txt          # 系统提示词（挂载为卷，修改无需重建镜像）
├── skills/
│   ├── files/
│   │   ├── Dockerfile
│   │   ├── requirements.txt
│   │   ├── app.py              # FastAPI，文件 I/O 工具路由
│   │   ├── path_guard.py       # 防目录遍历（realpath 检查）
│   │   ├── file_ops.py         # 读写操作
│   │   ├── trash.py            # 软删除
│   │   └── git_ops.py          # GitPython 封装
│   ├── runner/
│   │   ├── Dockerfile          # 非 root（uid 1001 runner）
│   │   ├── requirements.txt
│   │   ├── app.py              # FastAPI，code_exec/shell_exec/skill CRUD/skill_run
│   │   ├── sandbox.py          # subprocess 沙箱执行器（含超时）
│   │   └── skill_registry.py   # 技能 CRUD + 注册持久化 + 依赖管理 + 沙箱执行
│   └── websearch/              # ← 联网搜索服务（可选）
│       ├── Dockerfile
│       ├── requirements.txt    #    fastapi, httpx, beautifulsoup4, readability-lxml
│       └── app.py              #    web_search（调 SearXNG）+ web_fetch（抓取网页正文）
├── data/
│   ├── workspace/              # Agent 可操作的文件区（已 git init）
│   │   ├── data/               # 用户数据目录（CSV, Excel, JSON）
│   │   ├── docs/               # 文档目录（PDF, Word, 文本文件）
│   │   ├── reports/            # AI 生成的报告输出
│   │   ├── skills/             # 动态工具目录（AI 创建的工具脚本存这里）
│   │   │   ├── README.md       # 技能文件格式说明
│   │   │   └── word_count.py   # 示例技能
│   │   └── .skill_registry/    # ← 技能注册配置持久化
│   │       ├── registry.json   #    已注册技能索引
│   │       └── skills/         #    每个技能的独立配置文件
│   ├── trash/                  # 软删除暂存区
│   └── logs/
│       └── audit.jsonl         # 审计日志
└── scripts/
    ├── quick-start.ps1         # 一键启动脚本（含 Web Search 选项）
    ├── quick-start.sh
    ├── check-env.ps1           # Windows 环境检查脚本
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
| Open WebUI | `pip install open-webui` 后 `open-webui serve` |
| Python 3.10+ | 运行 Open WebUI 所需，容器内 Python 由镜像提供 |

检查命令：

```powershell
docker --version
docker compose version
git --version
ollama list
```

---

### Step 1：创建项目骨架目录

```powershell
mkdir -p C:\Users\Administrator\local-ai-agent
cd C:\Users\Administrator\local-ai-agent

mkdir config\tools, gateway\prompts,
      skills\files, skills\runner,
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
GATEWAY_PORT=8400
SKILL_FILES_PORT=8100
SKILL_RUNNER_PORT=8200

# Windows / macOS Docker Desktop 用 host.docker.internal
# Linux 无 Docker Desktop 时改为宿主机 IP，或在 compose 里加 extra_hosts
OLLAMA_BASE_URL=http://host.docker.internal:11434
# 推荐：qwen3.5:27b（32K 有效上下文，工具调用与推理能力强）
# 也可使用更小模型如 qwen2.5:7b 或 qwen2.5-coder:7b
OLLAMA_MODEL=qwen3.5:27b

LOG_LEVEL=INFO
AUTO_GIT_COMMIT=true

# 联网搜索功能开关（需要 SearXNG + skill-websearch 服务）
ENABLE_WEBSEARCH=false

# skill-runner 执行超时（秒）
PYTHON_EXEC_TIMEOUT=30
SHELL_EXEC_TIMEOUT=15
```

> **坑**：模型名首次填的是 `llama3.2`，但 Ollama 本地没有这个模型。务必确认 `.env` 里 `OLLAMA_MODEL` 与 `ollama list` 输出完全一致。

> **坑（Windows）**：Windows 可能将 8000–8290 端口段保留给 Hyper-V / WinNAT，导致 Docker 绑定时报 `An attempt was made to access a socket in a way forbidden by its access permissions`。
> 排查方法：`netsh int ipv4 show excludedportrange`。如果 8000 或 8100 在保留范围内，调整 `.env` 中 `GATEWAY_PORT` 到非保留端口（推荐 8400+）。skill-files 和 skill-runner 使用 `expose` 而非 `ports`，只在内部网络暴露，不受此问题影响。

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

# gateway 服务
gateway/requirements.txt
gateway/llm_client.py
gateway/policy_engine.py
gateway/audit_logger.py
gateway/tool_registry.py      ← 扫描 config/tools/*.yaml 生成工具定义
gateway/tool_router.py
gateway/app.py
gateway/prompts/system.txt
gateway/Dockerfile

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

**gateway Dockerfile**：gateway 镜像不需要 `apt-get`（Python-slim 基础包够用），直接 `pip install`。

> **坑**：gateway 镜像无 `curl`。`docker-compose.yml` 里 gateway 的 healthcheck 必须用 Python 一行命令，不能用 `curl`：
>
> ```yaml
> healthcheck:
>   test: ["CMD", "python", "-c", "import urllib.request; urllib.request.urlopen('http://localhost:8000/health')"]
> ```

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
    expose: ["8200"]          # 仅内部暴露，不对宿主机开放

  skill-files:
    expose: ["8100"]          # 仅内部暴露

  agent-gateway:
    ports:
      - "${GATEWAY_PORT:-8400}:8000"   # 只有 gateway 对外
    volumes:
      - ./config:/config:ro            # 挂载 config/（含 tools/ YAML）
      - ./gateway/prompts:/app/prompts:ro
    environment:
      - SKILL_WEBSEARCH_URL=http://skill-websearch:8300  # 联网搜索后端
      - ENABLE_WEBSEARCH=${ENABLE_WEBSEARCH:-false}      # 联网搜索开关
    depends_on:
      skill-files:
        condition: service_healthy
      skill-runner:
        condition: service_healthy

  # ── 以下为可选的联网搜索服务（通过 profiles 控制） ──

  searxng:
    image: searxng/searxng:latest
    profiles: [websearch]         # 仅在 --profile websearch 时启动
    volumes:
      - ./config/searxng:/etc/searxng  # 注意：不能加 :ro，SearXNG 启动时需要 chown
    expose: ["8080"]
    healthcheck:
      test: ["CMD", "wget", "--spider", "-q", "http://localhost:8080/healthz"]

  skill-websearch:
    profiles: [websearch]
    expose: ["8300"]
    depends_on:
      searxng:
        condition: service_healthy

networks:
  agent-net:
    driver: bridge              # 所有服务共享，与宿主机隔离
```

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

**基础启动（3 个核心服务）：**

```powershell
docker compose up -d
docker compose ps
```

**启用联网搜索（5 个服务）：**

```powershell
# 需先在 .env 中设置 ENABLE_WEBSEARCH=true
docker compose --profile websearch up -d
docker compose ps
```

> 也可以使用 `scripts/quick-start.ps1` 一键启动脚本，会交互式询问是否启用联网搜索。

等约 45 秒后，所有服务均应显示 `healthy`。

验证完整工具链（按顺序）：

```powershell
$base = "http://localhost:8400"

# 1. 文件写入（走 skill-files 后端）
Invoke-RestMethod -Method Post "$base/tool" `
  -ContentType "application/json" `
  -Body '{"tool":"file_write","params":{"path":"/workspace/data/test.txt","content":"hello"},"session_id":"test"}'

# 2. 文件读取
Invoke-RestMethod -Method Post "$base/tool" `
  -ContentType "application/json" `
  -Body '{"tool":"file_read","params":{"path":"/workspace/data/test.txt"},"session_id":"test"}'

# 3. 代码执行（走 skill-runner 后端）
Invoke-RestMethod -Method Post "$base/tool" `
  -ContentType "application/json" `
  -Body '{"tool":"code_exec","params":{"code":"print(1+1)"},"session_id":"test"}'

# 4. 列出已注册的动态工具
Invoke-RestMethod -Method Post "$base/tool" `
  -ContentType "application/json" `
  -Body '{"tool":"skill_list","params":{},"session_id":"test"}'

# 5. 验证 OpenAI-compatible 模型列表
Invoke-RestMethod "$base/v1/models"
```

> **注意**：PowerShell 的 `curl` 实际上是 `Invoke-WebRequest` 的别名，处理 JSON body 时引号转义非常麻烦。建议始终使用 `Invoke-RestMethod`，更干净。

---

### Step 8：接入 Open WebUI

1. 浏览器打开 `http://localhost:8888`，登录。
2. 进入 **Settings → Connections → Add Connection**，填写：
   - **类型**：选 **OpenAI**（不是 OpenAPI，不是 Ollama）
   - **URL**：`http://localhost:8400/v1`（**必须加 `/v1` 后缀，端口 8400**）
   - **API Key**：随便填一个字符串（gateway 不校验，但 Open WebUI 要求非空）
3. 保存后，在模型列表里应出现 `agent:qwen3.5:27b`（或 `.env` 中配置的模型名）。
4. 新建对话，选择该模型，即可通过 gateway 进行工具调用。

> **坑：类型选 OpenAPI 会失败**
>
> Open WebUI 有两个相近选项：OpenAI 和 OpenAPI。必须选 **OpenAI**，选 OpenAPI 走的是 swagger spec 格式，会报格式不兼容的错误。

> **坑：URL 必须加 `/v1`**
>
> 如果填 `http://localhost:8000`（少了 `/v1`），Open WebUI 会拼接出 `/v1/models`、`/v1/chat/completions`，路径正常；但如果基础 URL 本身已含 `/v1`，则会变成 `/v1/v1/...` 导致 404。总之：base URL 固定填 `http://localhost:8000/v1`，不多不少。

> **坑：发消息后 gateway 日志没有请求，只有 /health**
>
> 这通常意味着 Open WebUI 把消息发到了直连 Ollama（另一个已配置的连接），而不是 gateway。
> 根因：两个连接里的模型名完全相同（都叫 `qwen2.5:7b`）时，Open WebUI 会按第一个来。
> 解决：gateway 的 `/v1/models` 返回的模型 id 改为 `agent:qwen2.5:7b`，与 Ollama 原始名区分开，发消息时明确选 `agent:qwen2.5:7b`。

---

## 五、系统提示词调优

系统提示词位于 `gateway/prompts/system.txt`，以卷挂载方式加载，gateway 每次请求时动态读取，修改后无需重建或重启即生效。

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
# 查看实时日志（所有服务）
docker compose logs -f

# 只看 gateway 日志（工具调用 agentic loop 记录在这里）
docker compose logs -f agent-gateway

# 只看 skill-runner 日志（代码执行、技能 CRUD 操作）
docker compose logs -f skill-runner

# 只看 skill-files 日志
docker compose logs -f skill-files

# 只看搜索相关日志（需要已启用 websearch profile）
docker compose logs -f skill-websearch searxng

# 重建单个服务（代码有改动时）
docker compose build agent-gateway
docker compose up -d --no-deps agent-gateway

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

# 查看已注册的动态工具
Invoke-RestMethod -Method Post http://localhost:8400/tool `
  -ContentType "application/json" `
  -Body '{"tool":"skill_list","params":{}}'

# 查看特定技能详情
Invoke-RestMethod -Method Post http://localhost:8400/tool `
  -ContentType "application/json" `
  -Body '{"tool":"skill_info","params":{"skill_name":"example_skill"}}'
```

---

## 七、可用工具一览

所有工具定义存放在 `config/tools/*.yaml`，gateway 启动时自动加载。添加新工具只需新建 YAML 文件并重启 gateway。当前共 **18 个工具**。

### 文件操作工具（skill-files 后端）

| 工具 | 必填参数 | 说明 |
|---|---|---|
| `file_write` | `path`, `content` | 创建或覆盖文件，自动 git commit |
| `file_read` | `path` | 读取文件内容 |
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

> **工具调用预算**：为防止模型反复搜索导致上下文溢出，gateway 对 `web_search` 和 `web_fetch` 设有硬性调用次数上限。达到上限后工具定义会从候选列表中移除，强制模型用已有结果回答。

**路径规则**：
- 所有文件路径必须以 `/workspace` 开头，超出范围的请求会被 `PathGuard` 拦截返回 403
- 用户数据存 `/workspace/data/`，动态工具脚本存 `/workspace/skills/`
- `/workspace/skills/` 下的文件只能写入/更新，不能通过 gateway 删除或重命名移出

---

## 八、踩坑汇总

| 问题 | 现象 | 解决方案 |
|---|---|---|
| Docker Hub 连接超时 | `pull` 卡住或报 i/o timeout | 开 VPN 全局模式，Docker Desktop 配置代理 |
| apt-get 502 / EOF | `trixie-updates` 源构建失败 | Dockerfile 手动覆盖 `sources.list`，只保留 `trixie` 和 `trixie-security` |
| BuildKit 缓存损坏 | 构建 DeadlineExceeded 或拉缓存层失败 | `docker builder prune -af` 后 `--no-cache` 重建 |
| gateway healthcheck 一直失败 | 容器反复重启，日志报 `curl not found` | healthcheck 改用 `python -c "urllib.request.urlopen(...)"` |
| Open WebUI 连接失败 | 加完连接后模型列表为空 | 类型选 **OpenAI**（非 OpenAPI），URL 加 `/v1` 后缀 |
| 消息发到了直连 Ollama | gateway 日志只有 `/health`，无工具调用 | gateway 返回的模型 id 改为 `agent:qwen2.5:7b`，与原始 Ollama 模型名区分 |
| 系统提示打包进镜像 | 修改 `system.txt` 后行为不变 | `docker-compose.yml` 中把 `./gateway/prompts` 挂载为卷 |
| 模型回英文，不调用工具 | 用中文对话但模型回英文，且只解释操作步骤而不实际执行 | 系统提示词加语言规则 + 强制工具调用说明 + 工具列表 |
| `CommitRequest` NameError | skill-files 启动报 `NameError: name 'CommitRequest' is not defined` | 多处替换时误将两个 Pydantic 模型合并，需将 `RenameRequest(src, dst)` 和 `CommitRequest(message)` 分开独立定义 |
| 代码改了但容器行为没变 | 构建走了缓存跳过 COPY 层 | 确认用 `docker compose build`（而非只执行 `up`），必要时加 `--no-cache` |
| `.env` 模型名与 Ollama 不一致 | gateway 调用 Ollama 报 404 或模型不存在 | 用 `ollama list` 确认模型名，`.env` 里 `OLLAMA_MODEL` 完全对应 |
| Windows 端口被 Hyper-V 保留 | Docker 绑定端口 8000/8100 报 `access permissions` 错误，或 `open-webui serve` 绑定 8080 报 `[Errno 13]` | 用 `netsh int ipv4 show excludedportrange protocol=tcp` 查看保留范围（当前为 7653–8390）；gateway 改 `GATEWAY_PORT=8400`；Open WebUI 用 `--port 8888`；skill-files/runner 用 `expose` 不绑宿主机 |
| 回复一次性全部弹出，无逐字效果 | Open WebUI 出现回复前有明显等待，内容一下子全部显示，无打字机效果 | gateway 的 `oai_chat_completions` 原先即使设置 `stream: true` 也只发一个大 SSE chunk。需实现真正的流式：工具轮次同步跑完后，最终回复通过 `llm.chat_stream()` 逐 token 发送；无工具调用时用 `asyncio.sleep` 模拟打字分块发送 |
| 模型在文字回复里输出原始 JSON 工具调用 | 回复中出现 `{"name": "file_list", "arguments": {...}}` 的 JSON 代码块 | 小参数模型有时会把 function-call 的 JSON 也复制到文字内容里。在 `system.txt` 的 `CRITICAL EXECUTION RULES` 中加第 4 条：`NO RAW JSON IN TEXT — 工具调用通过 function-call 机制静默完成，回复里只用 inline code 引用工具名` |
| SearXNG 启动失败（unhealthy） | `container searxng is unhealthy`，日志报 `chown` 权限错误 | SearXNG 配置卷 `./config/searxng:/etc/searxng` **不能加 `:ro`**，启动时需要 `chown` 修改配置目录权限 |
| SearXNG 端口不对 | 连接 8888 超时，搜索无结果返回 | SearXNG 官方 Docker 镜像监听 **8080** 端口（非常见的 8888），health check 和内部引用都要用 8080 |
| `lxml_html_clean` 缺失 | skill-websearch 启动后 `web_fetch` 报 `ImportError: lxml.html.clean` | `readability-lxml` 依赖的 `lxml.html.clean` 已从 lxml 主包拆分，需在 `requirements.txt` 中显式添加 `lxml_html_clean` |
| AI 反复搜索不回答 | 搜索 5+ 轮后上下文溢出，回复截断或乱码 | 小参数模型（27B 以下）指令遵循能力弱，忽略系统提示中的搜索限制。需在 gateway 层实现 **硬性工具调用预算**（`web_search: 3`, `web_fetch: 2`），超额后从候选工具列表中移除 |
| 搜索结果包含视频/多媒体 | 搜索返回 YouTube 等视频链接，`web_fetch` 无法提取有效内容 | 当前 `web_fetch` 只能处理 HTML 文本页面，视频/音频/图片等多媒体内容无法提取。建议在 SearXNG 配置中排除视频类引擎，或在搜索 query 中指定 `site:` 限定文本源 |

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

然后在对应的后端服务（`skills/runner/app.py` 或 `skills/files/app.py`）添加对应路由，最后重启 gateway：

```powershell
docker compose restart agent-gateway
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

预算在每次用户消息请求时重置。实现位于 `gateway/app.py` 的 `_TOOL_BUDGETS` 和 SSE 流循环中。

### 代理配置（可选）

如需通过代理访问外部搜索引擎，在 `docker-compose.yml` 的 `searxng` 服务中添加环境变量：

```yaml
environment:
  - HTTP_PROXY=http://proxy:port
  - HTTPS_PROXY=http://proxy:port
```

---

## 十三、Open WebUI 输出结构

agent-gateway 通过 SSE 流式推送三种类型的内容到 Open WebUI，在聊天界面中按序渲染：

### 1. 思维链（Thinking）

```html
<think>
AI 的内部推理过程...
分析用户意图，决定调用哪些工具...
</think>
```

- Open WebUI **原生支持** `<think>` 标签，渲染为可折叠的 "🔎 Explored" 区块
- 用户可以展开查看 AI 的完整思考过程
- 来源：LLM 返回的 `thinking` / `reasoning` 字段

### 2. 技能调用状态

```html
<details>
<summary>🔧 技能调用</summary>

- 📂 `file_list` → 目录: /workspace/data — ✅ 成功 (0.2s)
- 📄 `skill_run` → 技能: pdf_page_count — ✅ 成功 (1.5s)

</details>
```

- 使用 HTML `<details>` 标签，Open WebUI 渲染为可折叠块
- 默认折叠，用户可点击展开查看工具调用详情
- 显示每个工具的名称、关键参数、执行结果和耗时
- 来源：gateway 在工具执行前后注入到 SSE 流中

### 3. 回答内容

标准 Markdown 格式的最终回答，支持：
- 标题、列表、表格、代码块
- 数学公式（LaTeX）
- 图片引用

来源：LLM 返回的 `content` 字段，逐 token 流式推送

### 渲染效果示例

在 Open WebUI 中，一个完整回复的视觉呈现：

```
🔎 Explored（可折叠，点击展开查看思考过程）

🔧 技能调用（可折叠，点击展开查看工具执行详情）

这是 AI 的最终回答...
使用 Markdown 格式排版。
```

### 技术实现

输出方案采用 **SSE 流式注入**，而非分离汇总：

- 保持 SSE 实时流式传输的优势（逐 token 显示，无等待感）
- 工具状态通过 `_execute_tools_with_status()` 函数注入到流中
- 不需要自定义前端协议，完全复用 Open WebUI 的 Markdown + HTML 渲染能力
- 三段内容按时间顺序自然排列：先思考 → 再执行工具 → 最后回答
