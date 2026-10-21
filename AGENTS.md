# AGENTS.md — 本仓库协作规则

桌面 WebView 缓存固定放在已忽略的 `data/private/desktop-webview/`，保持 private_mode；不得依赖临时目录回退到仓库根。清理旧缓存前须正常关闭占用窗口，不删除正在使用的配置目录。

> 给在本仓库工作的 AI 协作者与人协作者。**先读本文再动手。**

## 铁律：唯一入口、唯一数据

1. **用户准入入口只有一个**：根目录 `启动.bat` → `scripts/start-daily.ps1`（BFF @ 127.0.0.1:9510，同源托管 Web 界面）。任何新功能、测试、人工验收都必须能通过这同一个入口使用；**禁止**新增或复活第二条启动路径（旧的 quick-start / start-in1 / run_in1 / compose.in1 / TUI / Ink CLI 已全部移除）。`-Desktop` 只是同一入口的展示形态（pywebview 桌面窗口承载同一页面），不是第二条路径。
2. **数据只有一份**：会话库 `data/conversations.db`、工作区 `data/workspace/`、模型账本 `data/model-calls.sqlite`。隔离/验收数据通过**独立会话 + 独立股票状态目录**实现，不要再造独立库。历史 IN1 数据已并入日常库（`data/_archive/in1/` 留有原始备份）。
   命名工作区、目录根记录及会话 `workspace_id` 同样保存在 `conversations.db`；一个工作区可有多个会话和多个本机目录。默认托管目录仍为 `data/workspace/`；其他工作区托管内容在其 `.harness/<workspace_id>/` 下，外部目录直接挂载为 `/roots/<root_id>`，不复制源目录。默认容器遮蔽 `.harness`，不能访问其他工作区托管内容。
   会话研究方法选择是 `conversations.db` 的增量字段；不能另建设置库。股票任务的方法正文与版本在入队时由股票仓库冻结。
3. **退出也只有一条路径**：页面"退出"按钮或关闭页面（约 15 秒自动全退，含 docker 容器，见 `core/auto_exit.py`、`bff/app.py` 的 `_request_stack_shutdown`）。不要再加"只停某一层"的用户可见按钮。
   运行由后端持有，观察连接断开或切换会话不取消执行。只有 `/api/keepalive` 的实际页面连接计入关页宽限；脚本运行流和兼容心跳不保活。宽限结束先停止接收新运行、保存聊天及股票回执，再请求唯一启动器停止容器。收尾未确认时保留服务、执行锁和具体错误，不宣称已退出。
4. 页面上的服务管理只以**技能开关**形式存在（`core/skills_service.py` 联网搜索；`core/stock_service.py` 股票后台=股票技能开关）。新增可开关能力时沿用这个模式：持久化开关 + 进程/容器生命周期 + 工具注册表动态翻转（`apply_to`）。
5. 启动器启动本地 Ollama 时须读取已连接股票仓库 `config/local.json` 的回环端口，并将同一地址传给聊天后端；不要写死 `11434` 与股票侧分叉。
6. Docker 容器生命周期由唯一启动脚本进程处理：`scripts/start-daily.ps1` 在本进程内监听 `127.0.0.1:9511`，用私密令牌接受固定操作，并由该进程直接执行 Docker CLI。BFF 只能通过本机控制接口请求操作，不得派生 Docker CLI；股票后台端口不得配置为 `9511`。启动器进程须保持运行，页面完整退出时由它停止所有工作区容器组（含遗留组）并结束。工作区容器配置由 `scripts/workspace_compose.py` 生成到 `data/private/`；固定 `workspace_sync` 操作只接受工作区 ID，启动器读取已保存配置，不能接受可执行文件或任意命令。

## 常用命令

```powershell
.\scripts\start-daily.ps1        # 启动（= 双击 启动.bat）；重复运行只开浏览器
.\scripts\start-daily.ps1 -Desktop # 同一入口，改用桌面窗口显示页面（关窗=关页面）
.\scripts\start-daily.ps1 -Build # 改了 skills/** 后必须用这个重建工具容器镜像
.\scripts\start-daily.ps1 -AcceptanceState 'simulation/in45-method-acceptance-20261010' # 同一入口的验收配置核对；须已保存对应独立目录且worker=false
.\scripts\check-env.ps1          # 环境自检
.\scripts\stop-backend.ps1       # 应急：只停聊天后端（页面打不开时用）
.\.conda\python.exe -m unittest discover -s tests   # 离线测试（当前 258 项、2 项跳过（2026-10-09））
npm run build --prefix apps/web  # 前端构建（BFF 托管 apps/web/dist）
docker compose up -d             # 工具容器（websearch profile 由页面开关管理）
```

## 改完代码后怎么让它生效（三种，别搞混）

验收启动使用同一 `start-daily.ps1 -AcceptanceState <已保存的独立股票状态目录>`；它在任何启动副作用前核对目录、技能开启、任务领取暂停及无旧栈监听，并在启动股票前再次核对。前置写入或回读失败立即停止，禁止继续启动；不能将工具调用返回对象当作退出码成功。该参数不另建入口、会话库或初始化账户，账户确认仍由页面人工操作完成。

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

因此页面"股票技能"的**启动**按钮在线时恢复工具，离线时由 127.0.0.1:9511 的启动器控制器执行固定的 stock_start 操作，再由聊天后端核验身份并恢复工具；BFF 始终不派生股票进程。控制器不可用时保存开启意图并给出重启启动器指引。**停止**仍走控制接口优雅关闭。`STOCK_LAUNCHER_PID` 只作诊断记录；后台是否可用一律以控制接口的 `status()` 为准（**不要用"端口有人在听"当身份判据**，那会把占用该端口的外来进程当成自己的后台）。

**排障顺序**：先确认后台是否由启动器启动（看启动器第 4 步是否打印 `[OK] started on 127.0.0.1:8765`）；若不是，重新双击 `启动.bat`。其次按下面的 SQLite 错误串分流：

| 日志里的错误 | 含义 | 处置 |
| --- | --- | --- |
| `unable to open database file` | 启动阶段就打不开库：多为 WAL 锁文件状态不一致 | 退出整个栈 → 删 `state.sqlite-shm` 与 `state.sqlite-wal`（**不要碰 `state.sqlite`**）→ 重启 |
| `attempt to write a readonly database` | 库能读不能写，同上成因（schema 写入阶段） | 同上 |

`state.sqlite-wal` 为 0 字节时删除无风险；非 0 时先整目录备份。库本身通常完好，可用 `PRAGMA integrity_check` 确认。

## 结构速览

质量优先研究：`conversations.db` v8保存 `task_evidence`，v9增量保存 `chat_runs` 与轻量事件；升级前生成对应 `.before-vN.sqlite` 备份。索引仅归档已授权的模型可见历史，按会话及云端/本地工作区范围隔离；原始消息不改写。`context_evidence_read` 仅能读取本轮已登记的证据。股票直连职责通过已鉴权的 `/api/internal/research-generation` 请求，供应商 UNKNOWN 不自动重发。当前实现及未验收项见股票仓库 `docs/计划.md`。

股票 C03 专业输出明确 `finish_reason=length` 时，股票侧固定同一发布版本提高输出额度；只有唯一启动器加载的工作进程取得独占锁后，才可恢复已保存的已知截断。网络结果未知和活跃并发调用不得自动接管或重发；不另加恢复启动路径。见 `docs/记录/2026-10-09-IN4.20-公开线索与只读会话补修.md`。

新冻结 C01/C02 分层节点明确 length 截断时，保留父回执并无损拆成子窗口。截断落盘后进程退出，仅在同一工作进程取得独占锁后恢复原截断回执，再交给分层检查点继续；不重发父请求或网络 UNKNOWN。该路径离线已验，在线状态以股票 docs/计划.md 为准。

公司研究阅读进度由股票侧的执行检查点提供；报告分列已通过窗口、计划窗口和全文综合状态。原文采集量不能替代AI阅读量，旧版没有总量时不得猜测；检查点扩展不新增数据仓库或绕过账户确认。

新冻结只读研究退出时在步骤边界让出执行：当前模型请求先取得回执，再保存同一任务检查点；`YIELDED` 是未完成步骤的内部状态，不是模型失败。Jobs 的账户运行器与 research.specialists.runner 必须共享同一暂停信号，不能用 `or` 只绑定其中一个。股票节点恢复沿用原请求 ID，UNKNOWN 不因此变为可重试。聊天重启后只可由用户从兼容检查点创建关联的新运行；原记录不改写。事件简报逐事件保存可读成果；旧任务仍按原通路解释。更新股票代码前同时确认任务及调用均已到终态，避免运行中的发布版本校验失配。

- `core/workspaces.py`：工作区持久化、稳定目录映射、配置快照与执行锁；文件读写受主机进程权限限制时，通过对应工作区工具容器完成，不可另起本机命令执行后端。每组使用独立网络与私密工具凭据，健康核验同时检查 ID、版本及鉴权；主机预检覆盖 NTFS junction。配置切换只阻止相关目录的任务。
- `core/ui_session.py`：唯一启动器创建页面会话凭据，通过 URL fragment 交给页面并换取 HttpOnly cookie；BFF API 不接受无凭据的容器侧请求。凭据文件只在 data/private，不可挂载给工具或返回状态 API；启动器就绪探测也须携带凭据。
- `bff/app.py`：HTTP 层（聊天、会话、工作区、模型、技能开关、股票服务管理、心跳、退出）。退出语义：**停止新运行 → 聊天与股票收尾落盘 → 唯一启动器确认停容器 → BFF退出**；失败保留服务和错误。
- `core/runtime.py`：`build_runtime()` 组装工具注册表/路由/模型目录；技能开关通过公开的 `load_backend` / `unregister_owner` 与 `set_backend` 更新自身贡献，保留其他提供者的工具及 registry 对象同一性。禁止重建整张注册表覆盖其他能力。当前仍有旧业务接线，不能将此适配步骤视为完整插件迁移。
- `bff/service.py`：`ChatSessionService` 是会话/工作区/流式门面；`exclusive_turn` 保证同工作区及目录重叠工作区串行执行，返回 409 与执行会话；不同目录的工作区可并行。技能切换期间聊天 409。目录配置每轮冻结，失败保留有效配置，用户明确检查应用后激活待生效配置。
- 前端 `apps/web/src/` 为组件化结构：`app/`（App.tsx 编排层 + 顶栏/会话抽屉/输入胶囊/阅读栏/工作台 Sheet/设置/阅读器等区块）、`panels/`（工作区、技能、股票、打包任务）、`ui/`（Collapse/Panel/Popover 等基元）、`lib/`（storage/format/appearance/transcript/report/notify/confirm）；`styles.css` 是唯一令牌来源（Apple 设计系统，旧三栏紫系已废弃）。界面无常驻侧栏：顶栏细条 + 全屏阅读栏 + 底部悬浮输入胶囊；会话抽屉与工作台 Sheet 两级浮层默认收起；唯一"退出"按钮在会话抽屉底部。
- 提示词在 `config/prompts/`（从旧 gateway/ 迁入；`core/config.py` 的 `PROMPTS_DIR` 指到这里）。普通聊天的正式系统内容只维护 `prompts/modules/01..08`（`08_research.txt` 是公开投研的触发、正文结构与交付规则）；`prompts/system.txt` 只是 modules 缺失时的兜底占位。自主研究的单独核对角色使用 `prompts/research_review.txt`，不作为普通聊天模块注入；它与原模块一起进入检查点兼容指纹。

聊天自主研究的evidence_requests由程序展开为原工具调用，不另起执行后端。evidence-index.json只登记实际收到的资料及模型标注；reviews保存同模型单独核对的候选稿/可见结果，不保存隐藏思维链、不进入正文。发布关联当前稿及证据指纹，不认证语义；核对UNKNOWN停止本轮且不重发。无进展收尾保留已有资料回读、改稿及核对，禁止新增联网需求；不能把阶段限制报成供应商额度不足。

## 约定

- 两个仓库直接工作在 `main`；提交小而可审阅，按批次（INx/N2 等）记录到股票仓库 `docs/计划.md`。
- 前端施工先看 `docs/UI设计笔记.md`（令牌、组件模式、动效与施工规则）；前端批次进度只维护股票仓库 `docs/计划.md`，主题方案见 `docs/深色模式落地计划.md`。
- 数据与密钥不落库提交：`.env`、`data/` 均 gitignore；密钥只经页面"模型设置"保存到 `data/private/model-keys.json`。
- 验收回归方法见 `docs/回归验证指南.md`；日常口径见 `docs/日常运行指南.md`；部署见 `本地AI-Agents部署流程.md`。改启动/退出/数据结构时必须同步这些文档与本文件。
- 涉及股票业务的改动先看 `docs/股票能力接入.md` 的模块归属：聊天侧只做模型路由、工具面与卡片展示；业务规则在股票仓库。

只读研究验收沿用工作台的任务执行开关：勾选“仅执行主动提交的只读研究”后启动同一工作进程，暂停日常调度及账户任务领取；停止、退出和恢复仍走原入口。该设置不会自动修改账户规则。

直连只读研究在现有 `model-calls.sqlite` 增量保存 RUNNING 与终态回执；首次迁移已有库前备份 `model-calls.before-research.sqlite`。不保存模型正文或隐藏思维链，不以累计次数阻断已授权研究。股票检查点仍负责重入和 UNKNOWN 隔离。

退出诊断：Windows 端口探测须容忍本地化编码和空 stdout，不得让辅助 netstat 诊断阻断股票优雅关闭、启动器容器停止及 BFF 退出；仍沿用唯一退出流程。

| 搜索工具更新 | 操作与核验 |
| --- | --- |
| 修改 skills/websearch 后 | 唯一入口 start-daily.ps1 -Build 同时构建搜索镜像；构建不改变搜索技能开关，启动仍由保存的开关决定。失败时停止启动，日志 data/logs/websearch-build.log |
| 遗留搜索容器与 Build | 构建成功后由启动器停止旧 skill-websearch 容器，再按原技能开关恢复；不能仅凭旧容器健康而继续运行旧镜像 |
| 生效核验 | 搜索容器 OpenAPI 的 WebSearchRequest 应含 page、offset、page_hash；通过内部搜索接口实测游标，不能只检查源码 |

只读研究验收的现有worker_start动作可带job_ids，仅配合research_only=true并在空闲时设置；执行范围保存在原股票状态meta，重启保持。普通不带模式的重复启动保留该范围；主动切换执行模式清除。暂停、退出及股票顶层启动仍沿唯一流程，不能另起验收执行器。

启动器在副作用前试绑空闲的 9510/9511；已有监听仍核对原身份。端口绑定失败立即提示 Windows 排除范围，后端提前退出立即报退出码及日志，不自动更换端口或重启系统网络服务。系统端口恢复需明确授权；详见 docs/记录/2026-10-15-固定端口启动恢复.md。

## 会话模型统一（2026-10-21）

模型选择属于conversation.model，顶栏修改立即持久化，不影响其他会话。每项运行冻结模型和实际生成参数；兼容续行也使用该快照。分析、写作、单独核对及新股票后台任务统一继承会话模型，不再读取股票独立简报模型或Qwen开关。可信execution_profile由BFF建立，经现有已鉴权内部通路使用，模型工具参数不能覆盖。云读授权仍按冻结授权和当前工作区共同核验，账户/交易规则不变。

旧模型/Dify股票任务仅可查看，不领取、启动、继续或迁移。本文上述截断恢复规则是历史机制说明，不授权恢复旧模型任务；旧UNKNOWN不重发。新任务缺少所属会话时明确拒绝，不能用默认股票模型兜底。

local_agent是聊天Agent内部的单次本地Gemma4:26b调用；无工具、发布、账户操作或递归权限，主模型已为Gemma4时不开放。只接受本轮已授权材料/登记证据，复用运行控制、轨迹和原模型账本；失败不切换、不下载，取消或缺终态保持UNKNOWN。
