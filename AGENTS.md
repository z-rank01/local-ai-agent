# AGENTS.md — 本仓库协作规则

> 给在本仓库工作的 AI 协作者与人协作者。**先读本文再动手。**

## 铁律：唯一入口、唯一数据

1. **用户准入入口只有一个**：根目录 `启动.bat` → `scripts/start-daily.ps1`（BFF @ 127.0.0.1:9510，同源托管 Web 界面）。任何新功能、测试、人工验收都必须能通过这同一个入口使用；**禁止**新增或复活第二条启动路径（旧的 quick-start / start-in1 / run_in1 / compose.in1 / TUI / Ink CLI 已全部移除）。
2. **数据只有一份**：会话库 `data/conversations.db`、工作区 `data/workspace/`、模型账本 `data/model-calls.sqlite`。隔离/验收数据通过**独立会话 + 独立股票状态目录**实现，不要再造独立库。历史 IN1 数据已并入日常库（`data/_archive/in1/` 留有原始备份）。
   会话研究方法选择是 `conversations.db` 的增量字段；不能另建设置库。股票任务的方法正文与版本在入队时由股票仓库冻结。
3. **退出也只有一条路径**：页面"退出"按钮或关闭页面（约 15 秒自动全退，含 docker 容器，见 `core/auto_exit.py`、`bff/app.py` 的 `_request_stack_shutdown`）。不要再加"只停某一层"的用户可见按钮。
4. 页面上的服务管理只以**技能开关**形式存在（`core/skills_service.py` 联网搜索；`core/stock_service.py` 股票后台=股票技能开关）。新增可开关能力时沿用这个模式：持久化开关 + 进程/容器生命周期 + 工具注册表动态翻转（`apply_to`）。
5. 启动器启动本地 Ollama 时须读取已连接股票仓库 `config/local.json` 的回环端口，并将同一地址传给聊天后端；不要写死 `11434` 与股票侧分叉。

## 常用命令

```powershell
.\scripts\start-daily.ps1        # 启动（= 双击 启动.bat）；重复运行只开浏览器
.\scripts\start-daily.ps1 -Build # 改了 skills/** 后必须用这个重建工具容器镜像
.\scripts\check-env.ps1          # 环境自检
.\scripts\stop-backend.ps1       # 应急：只停聊天后端（页面打不开时用）
.\.conda\python.exe -m unittest discover -s tests   # 离线测试（当前 213 项通过、1 项跳过）
npm run build --prefix apps/web  # 前端构建（BFF 托管 apps/web/dist）
docker compose up -d             # 工具容器（websearch profile 由页面开关管理）
```

## 改完代码后怎么让它生效（三种，别搞混）

| 改了什么 | 怎么生效 | 不生效时的表现 |
| --- | --- | --- |
| `core/**`、`bff/**`、`config/**` | 重启聊天后端（`stop-backend.ps1` 再 `start-daily.ps1`）；uvicorn 无 `--reload` | 新字段/新逻辑静默不生效；旧 Pydantic 模型会**静默忽略未知字段并返回成功** |
| `skills/**`（工具容器内代码） | **`start-daily.ps1 -Build`**（重建镜像并重建容器） | 容器仍跑旧代码：新工具参数被忽略（例如 `file_read` 的 `offset` 无效、直接返回全文） |
| `apps/web/**` | `npm run build --prefix apps/web` + 刷新页面 | 页面仍是旧 bundle |

改动工具面后，用容器自己的 schema 自查最快：`Invoke-RestMethod http://127.0.0.1:9101/openapi.json` 看 `ReadRequest` 是否含新参数。

### 股票后台由启动器启动，不由聊天后端派生（2026-09-28 起）

`scripts/start-daily.ps1` 的第 3 步以**顶层进程**启动股票后台（`Start-Process`），并在就绪后恢复任务执行；聊天后端**不再**自己派生它。原因与证据：

| 启动来源 | 结果 |
| --- | --- |
| 启动器 `Start-Process`（顶层进程） | ✅ 后台正常读写 `simulation/` |
| 聊天后端 `subprocess.Popen` 派生 | ❌ 子进程写股票仓库被拒 → SQLite `unable to open database file` |

**限制沿整棵进程树继承**，下面这些"绕一层"的写法**全部实测失败**，不要再试：

```
subprocess.Popen(列表) / cmd /c "…" / cmd /c start "" /b … / 写 .bat 再 cmd /c 该bat /
powershell Start-Process …（由聊天后端调用）
```

`schtasks`（计划任务）也被拒（`Access is denied`），不能用作兜底。

因此：页面"股票技能"的**启动**按钮只会给出明确指引（"请重新双击 启动.bat"），不再尝试派生；**停止**仍走控制接口优雅关闭，可用。要恢复后台，就重新走唯一入口。

`STOCK_LAUNCHER_PID` 由启动器写入聊天后端环境，`core/stock_service.launcher_owns_backend()` 用它区分"启动器拥有"与"无人拥有"，避免又退回派生路径。

### 仍要留意：WAL 残留会让后台启动失败

`data/logs/stock-service.err.log` 出现任一情形时，先清 WAL 锁文件：

```
sqlite3.OperationalError: unable to open database file          (打开阶段)
sqlite3.OperationalError: attempt to write a readonly database  (schema 写入阶段)
```

1. 退出整个栈，确认没有 `run_paper` 进程
2. 删掉状态目录下的 **`state.sqlite-shm`** 与 **`state.sqlite-wal`**。**不要碰 `state.sqlite`**
3. 重新启动

`state.sqlite-wal` 为 0 字节时删除无风险；非 0 时先整目录备份。成因是上次进程被强杀导致锁文件状态不一致，库本身通常完好（先用 `PRAGMA integrity_check` 确认）。

> **诊断史（避免重犯）**：这个问题曾被两次误判——先归因于"AI 会话的进程受约束"（对了一半：限制真实存在，但不止于 AI 会话），后归因于"WAL 残留"（也真实，但不是根因）。**根因是限制沿进程树继承**，所以任何从聊天后端派生的写法都会失败。判断依据只看一条：后台是否由**启动器顶层进程**启动。

## 结构速览

- `bff/app.py`：HTTP 层（聊天、会话、工作区、模型、技能开关、股票服务管理、心跳、退出）。退出语义：**先股票优雅收尾 → 停 docker 容器 → 杀本进程**。
- `core/runtime.py`：`build_runtime()` 组装工具注册表/路由/模型目录；技能开关通过替换 `tool_registry._tools` 与 `router._backend_urls` 动态生效（注意保持 registry 对象同一性，见 `stock_service.attach` 与 `skills_service.apply_to` 的注释）。
- `bff/service.py`：`ChatSessionService` 是会话/工作区/流式门面；`exclusive_turn` 保证单会话互斥，技能切换期间聊天 409。
- 前端 `apps/web/src/App.tsx` 是单页全部主界面；右栏自上而下：工作区、外观、模型/Provider/工具、状态（唯一"退出"按钮）、技能面板、股票技能面板。
- 提示词在 `config/prompts/`（从旧 gateway/ 迁入；`core/config.py` 的 `PROMPTS_DIR` 指到这里）。正式内容只维护 `prompts/modules/01..07`；`prompts/system.txt` 只是 modules 缺失时的兜底占位，不要往里写正式提示词。

## 约定

- 两个仓库直接工作在 `main`；提交小而可审阅，按批次（INx/N2 等）记录到股票仓库 `docs/计划.md`。
- 数据与密钥不落库提交：`.env`、`data/` 均 gitignore；密钥只经页面"模型设置"保存到 `data/private/model-keys.json`。
- 验收回归方法见 `docs/回归验证指南.md`；日常口径见 `docs/日常运行指南.md`；部署见 `本地AI-Agents部署流程.md`。改启动/退出/数据结构时必须同步这些文档与本文件。
- 涉及股票业务的改动先看 `docs/股票能力接入.md` 的模块归属：聊天侧只做模型路由、工具面与卡片展示；业务规则在股票仓库。
