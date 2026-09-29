# AGENTS.md — 本仓库协作规则

> 给在本仓库工作的 AI 协作者与人协作者。**先读本文再动手。**

## 铁律：唯一入口、唯一数据

1. **用户准入入口只有一个**：根目录 `启动.bat` → `scripts/start-daily.ps1`（BFF @ 127.0.0.1:9510，同源托管 Web 界面）。任何新功能、测试、人工验收都必须能通过这同一个入口使用；**禁止**新增或复活第二条启动路径（旧的 quick-start / start-in1 / run_in1 / compose.in1 / TUI / Ink CLI 已全部移除）。
2. **数据只有一份**：会话库 `data/conversations.db`、工作区 `data/workspace/`、模型账本 `data/model-calls.sqlite`。隔离/验收数据通过**独立会话 + 独立股票状态目录**实现，不要再造独立库。历史 IN1 数据已并入日常库（`data/_archive/in1/` 留有原始备份）。
   会话研究方法选择是 `conversations.db` 的增量字段；不能另建设置库。股票任务的方法正文与版本在入队时由股票仓库冻结。
3. **退出也只有一条路径**：页面"退出"按钮或关闭页面（约 15 秒自动全退，含 docker 容器，见 `core/auto_exit.py`、`bff/app.py` 的 `_request_stack_shutdown`）。不要再加"只停某一层"的用户可见按钮。
4. 页面上的服务管理只以**技能开关**形式存在（`core/skills_service.py` 联网搜索；`core/stock_service.py` 股票后台=股票技能开关）。新增可开关能力时沿用这个模式：持久化开关 + 进程/容器生命周期 + 工具注册表动态翻转（`apply_to`）。
5. 启动器启动本地 Ollama 时须读取已连接股票仓库 `config/local.json` 的回环端口，并将同一地址传给聊天后端；不要写死 `11434` 与股票侧分叉。
6. Docker 容器生命周期由唯一启动脚本进程处理：`scripts/start-daily.ps1` 在本进程内监听 `127.0.0.1:9511`，用私密令牌接受固定操作，并由该进程直接执行 Docker CLI。BFF 只能通过本机控制接口请求操作，不得派生 Docker CLI；股票后台端口不得配置为 `9511`。启动器进程须保持运行，页面完整退出时由它停止容器并结束。

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

### 股票后台起不来：先看是谁启动的它

**规则：股票后台只能由启动器以顶层进程启动，聊天后端不得派生它。** scripts/start-daily.ps1 第 4 步会按保存设置启动后台；运行期间页面开启股票技能时，BFF 通过 127.0.0.1:9511 的固定操作请求启动器执行同一 Start-Process，再验证 /health 与状态目录身份后启用工具。停止仍由 BFF 调用股票控制 API 优雅关闭。若本机服务控制器不可用，页面保留开启意图并提示通过唯一入口重启启动器。启动器窗口会在服务运行期间保持打开；正常退出请用页面“退出”或关闭页面。

实测（2026-09-28，同一可执行文件、同一用户）：

| 启动来源 | 结果 |
| --- | --- |
| 启动器 `Start-Process`（顶层进程） | ✅ 正常读写 `simulation/` |
| 聊天后端 `subprocess.Popen` 派生 | ❌ `PermissionError 13` → SQLite `unable to open database file` |

限制**沿整棵进程树继承**，以下"绕一层"的写法全部实测失败，不要再试；`schtasks` 亦被拒（`Access is denied`）：

```
subprocess.Popen(列表) / cmd /c "…" / cmd /c start "" /b … / 写 .bat 再 cmd /c 该bat /
powershell Start-Process …（由聊天后端调用）
```

因此页面"股票技能"的**启动**按钮在线时恢复工具，离线时由 127.0.0.1:9511 的启动器控制器执行固定的 stock_start 操作，再由聊天后端核验身份并恢复工具；BFF 始终不派生股票进程。控制器不可用时保存开启意图并给出重启启动器指引。**停止**仍走控制接口优雅关闭。STOCK_LAUNCHER_PID 记录启动器创建的股票后台进程，launcher_owns_backend() 用于确认返回的进程仍存活。

**排障顺序**：先确认后台是否由启动器启动（看启动器第 4 步是否打印 `[OK] started on 127.0.0.1:8765`）；若不是，重新双击 `启动.bat`。其次按下面的 SQLite 错误串分流：

| 日志里的错误 | 含义 | 处置 |
| --- | --- | --- |
| `unable to open database file` | 启动阶段就打不开库：多为 WAL 锁文件状态不一致 | 退出整个栈 → 删 `state.sqlite-shm` 与 `state.sqlite-wal`（**不要碰 `state.sqlite`**）→ 重启 |
| `attempt to write a readonly database` | 库能读不能写，同上成因（schema 写入阶段） | 同上 |

`state.sqlite-wal` 为 0 字节时删除无风险；非 0 时先整目录备份。库本身通常完好，可用 `PRAGMA integrity_check` 确认。

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
