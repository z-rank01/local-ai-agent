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
4. 页面上的服务管理只以**技能开关**形式存在（`plugins/websearch/service.py` 联网搜索；`plugins/finance/service.py` 股票后台=股票技能开关）。新增可开关能力时沿用这个模式：持久化开关 + 进程/容器生命周期 + 原服务适配器动态更新工具注册表。
5. 启动器启动本地 Ollama 时须读取已连接股票仓库 `config/local.json` 的回环端口，并将同一地址传给聊天后端；不要写死 `11434` 与股票侧分叉。
6. Docker 容器生命周期由唯一启动脚本进程处理：`scripts/start-daily.ps1` 在本进程内监听 `127.0.0.1:9511`，用私密令牌接受固定操作，并由该进程直接执行 Docker CLI。BFF 只能通过本机控制接口请求操作，不得派生 Docker CLI；股票后台端口不得配置为 `9511`。启动器进程须保持运行，页面完整退出时由它停止所有工作区容器组（含遗留组）并结束。工作区容器配置由 `scripts/workspace_compose.py` 生成到 `data/private/`；插件容器沿同一生成器和启动器，`plugin_prepare/start/stop/probe` 只接受已登记插件 ID、服务 ID 与工作区 ID，不能传构建命令、镜像名或挂载路径。服务端口保存在原会话库 capability_installs，环境资源参与哈希；可选服务使用独立 profile，完整退出停止全部 profile。固定 `workspace_sync` 操作只接受工作区 ID，启动器读取已保存配置，不能接受可执行文件或任意命令。

## 常用命令

```powershell
.\scripts\start-daily.ps1        # 启动（= 双击 启动.bat）；重复运行只开浏览器
.\scripts\start-daily.ps1 -Desktop # 同一入口，改用桌面窗口显示页面（关窗=关页面）
.\scripts\start-daily.ps1 -Build # 改了 skills/** 后必须用这个重建工具容器镜像
.\scripts\start-daily.ps1 -AcceptanceState 'simulation/in45-method-acceptance-20261010' # 同一入口的验收配置核对；须已保存对应独立目录且worker=false
.\scripts\check-env.ps1          # 环境自检
.\scripts\stop-backend.ps1       # 应急：只停聊天后端（页面打不开时用）
.\.conda\python.exe -m tests.run_offline # 离线驱动禁止真实HTTP（1080项、21项跳过，2026-11-03实测；Windows asyncio需可用本机socket）
npm run build --prefix apps/web  # 前端构建（BFF 托管 apps/web/dist）；系统 TEMP 权限拒绝时用同一入口 -RebuildWeb
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
- `core/runtime.py`：`build_runtime()` 组装工具注册表/路由/模型目录，并按插件清单登记服务适配器；能力启停仅更新自身贡献，保留其他提供者工具与 registry 对象同一性。金融编排、提示和报告贡献归金融包；兼容桥仍保留原会话路由，不能据此宣称所有传输均已完成上下文绑定迁移。
- `bff/service.py`：`ChatSessionService` 是会话/工作区/流式门面；`exclusive_turn` 保证同工作区及目录重叠工作区串行执行，返回 409 与执行会话；不同目录的工作区可并行。技能切换期间聊天 409。目录配置每轮冻结，失败保留有效配置，用户明确检查应用后激活待生效配置。
- 前端 `apps/web/src/` 为组件化结构：`app/`（App.tsx 编排层 + 顶栏/会话抽屉/输入胶囊（含 A 能力栏）/阅读栏/工作台 Sheet/设置/阅读器等区块）、`panels/`（工作区、技能、股票、打包任务）、`ui/`（Collapse/Panel/Popover 等基元）、`lib/`（storage/format/appearance/transcript/report/notify/confirm/flags）；`styles.css` 是唯一令牌来源（Apple 设计系统，旧三栏紫系已废弃）。能力管理侧栏 v2 在 `panels/capabilities/CapabilityManager.tsx`，旧界面保留为 `SkillsPanelLegacy.tsx`；两侧与 Composer 共用 App 的 `useCapabilityRegistry` 与 selection，`VITE_CAPABILITY_BAR_A` 是能力面总开关，关闭时同时回退 Composer 与侧栏。界面无常驻侧栏：顶栏细条 + 全屏阅读栏 + 底部悬浮输入胶囊；会话抽屉与工作台 Sheet 两级浮层默认收起；唯一"退出"按钮在会话抽屉底部。
- 提示词在 `config/prompts/`（从旧 gateway/ 迁入；`core/config.py` 的 `PROMPTS_DIR` 指到这里）。普通聊天通用系统内容维护在 `prompts/modules/01..07`；搜索与金融专属内容在各自 `plugins/<id>/prompts/`，由清单 `prompts` 声明并按本轮登记工具选择。新增能力提示不改通用 Agent 分支。插件提示文件和触发声明一起进入检查点兼容指纹；`prompts/system.txt` 只是 modules 缺失时的兜底占位。自主研究的单独核对角色使用 `plugins/finance/prompts/review.txt`，不作为普通聊天模块注入；它与原模块一起进入检查点兼容指纹。

历史v1/v2研究的evidence_requests由程序展开为原工具调用，不另起执行后端；新研究使用research_contract_version=3，由finance_evidence、finance_calculation、finance_analysis、finance_review四个声明式角色分别填写摘录、已校验计算、claims及核对建议。作者等待整批终态后以decision_claim_ids和实际artifact引用编辑报告，不能覆盖角色原始成果或另开直接取证／计算路径。金融映射位于plugins/finance/research/roles.py，通用调度不在金融插件内。evidence-index.json只登记实际收到的资料及模型标注；reviews保存同模型单独核对的候选稿/可见结果，不保存隐藏思维链、不进入正文。方法正文、父稿与历史问题明确交接；供应商序列化后可见材料沿原task_evidence归档并关联实际调用，不能把下载/保存冒充已纳入请求。brief_review或brief_publish先返回建议；brief_publish首次REVIEW_READY不发布，作者带review_ref与简短review_response明确发布，可保留有依据的异议，不要求意见清零。v3核对指纹覆盖原问题、候选稿、方法、角色成果与核心claims选择。同版回执复用，失败/UNKNOWN不重发；旧契约不改写。详见docs/金融研究Recipe契约.md。研究不按累计时长、轮数、搜索/抓取次数或无进展计数强制收尾；进展计数仅作诊断，后台提交按已授权目标选择，受理不代表执行，不开启日常worker或恢复账户/旧任务。STOCK_WINDOW_SECONDS废弃；broker v2无研究deadline/max_steps，已保存v1保持原契约，不自动升级或重发。单次网络超时、当前授权、账户准入及用户停止仍生效。

## 约定

新研究自主选择是否使用方法，不能以方法读取隐藏已授权工具或拒绝取证、核对与发布；选用时仍冻结版本。收到新核对反馈时建立recipe-author-v2交接边界，包含全部已返回证据、方法、最近提交稿及反馈；此后完整保留连续操作和新文件选择，不每步重置消息，也不走旧工具批次预览。边界缺失时保留原视图，完整可见历史保存在原task_evidence并可回读，不保存隐藏推理。最近提交稿不冒充后续另存的新工作稿，工作稿刷新不自动取得核对资格，发布仍匹配当前稿与证据版本。

独立核对排除review自查及editorial_notes修订声明，保留已知冲突和资料限制；标题、截止日和开头单列呈现。核心陈述claim_checks与claim_provenance沿原reviews及反馈返回，程序只观察原句和引文是否实际出现，模型判断和原文观察日期不因此获认证；不得把引用匹配当作事实正确或新增发布否决权。建议式补读由作者自主选择原工具，不宣称自动执行。

独立核对系统消息只采用核对角色，不继承作者的研究启动、工具编排及正文发布指令；同一冻结模型及完整材料交接不变。作者按原问题和Recipe目标判断核心缺口、可选拓展及是否发布，核对意见不自动增加用户要求，不新增计数收尾或意见清零门槛。

- 两个仓库直接工作在 `main`；提交小而可审阅，按批次（INx/N2 等）记录到股票仓库 `docs/计划.md`。
- 前端施工先看 `docs/UI设计笔记.md`（已压缩的前端设计摘要：令牌、组件模式、动效、A/manager 与深色规则）；前端批次进度只维护股票仓库 `docs/计划.md`。
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

能力安装状态增量保存于原 `conversations.db` 的 v10 `capability_installs`，会话级能力选择为 v11 `conversations.capability_ids`（`skill:<name>` / `plugin:<id>` JSON）；升级前自动生成 `.before-v11.sqlite`。回退代码不能用旧备份覆盖升级后新消息。空选择不缩窄运行目录，非空只收窄本轮 Skill/Plugin 发现目录，不摘工具 schema、不改权限。目录发现不再作为工具解锁门槛；无需先调用skill_list/skill_info才能读取已授权文件。模型自主选择是否使用方法，选用时仍读取本轮固定版本。方法包的启停、待激活版本和回退按工作区隔离；运行中的版本引用不随目录更新而改变。

恢复按钮使用原股票工作进程的 `recovery_only_worker` 范围，只执行恢复核对，不领取普通任务，也不修改 `research_only_worker` 或限定任务列表。完成或阻断后暂停，账户恢复确认仍由页面人工完成；重启沿唯一启动器，不另起恢复执行器。

一次性恢复不得持久化 `daily-services.json.worker=true`；网络回执未知也保持下次启动不自动领取任务。镜像内插件资源在 `plugins/*/runtime`、`tools` 及清单中维护，修改后仍由唯一入口 `-Build` 生效。文件服务构建使用根目录白名单 `.dockerignore`，只复制服务与插件代码/契约，不把 data、密钥或整个宿主仓库挂载给容器。资料索引缓存属于工作区 `.capabilities/document-index`，可重建，不能成为第二份原始资料库。

Harness 当前实现：正式聊天的方法型 Skill 通过 Runner 的版本接口复制内容快照，运行检查点保存版本引用；更新原目录不影响该快照，旧式 Python Skill 暂沿兼容通路。`bff/skill_management.py` 的目录、差异和导入先走原工作区恢复流程；历史差异接受 before/after，卸载后仍可比较保留版本。`core/agent.py` 通过 `derive` 显式派生冻结依赖与回合配置，禁止以 `__dict__` 共享宿主内部状态；普通工具执行由宿主统一实现，`plugins/finance/loop.py` 仅在钩子中保留金融专属分发与发布。首版每个运行只允许一个回合编排者和一个研究会话提供者，冲突在激活时提示停用顺序，不静默覆盖。core 中旧导入兼容保留，不另建执行后端。skills/** 改动须唯一入口 `-Build` 重建并重启 BFF；部署与真实模型验收状态只看股票仓库 `docs/计划.md`。

模型选择属于conversation.model，顶栏修改立即持久化，不影响其他会话。每项运行冻结模型和实际生成参数；兼容续行也使用该快照。分析、写作、单独核对及新股票后台任务统一继承会话模型，不再读取股票独立简报模型或Qwen开关。可信execution_profile由BFF建立，经现有已鉴权内部通路使用，模型工具参数不能覆盖。云读授权仍按冻结授权和当前工作区共同核验，账户/交易规则不变。

模型发现只接收有效数值容量，不接受远程options、端点或权限。明确输出配置优先，无配置时采用该模型已声明容量，未知则用供应商默认；实际输出参数进入检查点和execution_profile，后续目录/全局设置变化不改写快照。缺少输出参数快照的旧云端记录不可原地续行。金融核对的length保存TRUNCATED及原生回执元数据，不保存隐藏推理、不自动重发，不以研究预算耗尽解释。

作者交接的可选工作稿回读发生传输失败时，保存真实失败回执并标READ_FAILED；旧快照单列，不冒充当前稿已读，材料清单明确未纳入。模型仍可用原工具选择检查；不内部循环重读，不吞权限拒绝或检查点保存失败。源码及提示变化仍进入兼容指纹，旧记录不改写。

工具真实回执先进入原协议归档；随后投影或检查点保存失败时保留已返回结果，不给同一调用追加泛化中断协议或覆盖真实参数错误。工具行单列收尾未完成，整轮仍按实际错误失败；不将检查点改安全、不自动重发。未收到回执的步骤仍保留中断或UNKNOWN。

金融发布的同类量比值按单位归一：倍用小数比例，%用百分数；明确单位换算常数沿原算式，不能把0.15直接标成0.15%。派生值可由程序填算，显式不符返回索引，不修改原稿或认证财务含义。发布计算、工具契约和提示变化随金融贡献指纹检查；历史报告及broker契约不改写。

分红分析分开记录所属盈利期、宣告/实施状态、实际支付主体与时间、现金收支及可动用资金。覆盖比率和可分配利润不证明实际支付来源，不能据此认证已依赖或未依赖存量现金；资料不足保留来源未知。截止日之后的行情或新披露与该时点结论分开，不能用实时行情代替历史行情。该方法与核对边界归金融插件提示，进入原兼容指纹，不新增发布否决权或执行限制。

单独核对仅对目录中已核实支持的型号派生json_object输出请求；能力声明随模型快照冻结，不修改原会话客户端或普通聊天/写作格式，不覆盖显式冲突设置，不关闭思考。实际格式参数进入核对记录；空白、非法结构或解析失败仍保留原响应和真实失败，不自动重发、发布或进行事实修补。

旧模型/Dify股票任务仅可查看，不领取、启动、继续或迁移。本文上述截断恢复规则是历史机制说明，不授权恢复旧模型任务；旧UNKNOWN不重发。新任务缺少所属会话时明确拒绝，不能用默认股票模型兜底。

local_agent是聊天Agent内部的单次本地Gemma4:26b调用；无工具、发布、账户操作或递归权限，主模型已为Gemma4时不开放。只接受本轮已授权材料/登记证据，复用运行控制、轨迹和原模型账本；失败不切换、不下载，取消或缺终态保持UNKNOWN。

通用子Agent通过 `agent_batch_create/status/result/stop` 提供一层委派。角色声明在 `config/subagents.json` 或插件 `agent_roles` 中，工具只能收窄，禁止递归委派、创建研究或发布。可选能力声明在 `ToolRegistry.declared_tools` 校验，执行只使用本轮实际注册的工具子集。Harness BatchRunner只依赖执行协议与可选成果回调，按真实本地终态释放批次和依赖等待；保存或映射失败须返回错误，不能悬置。Core executor/store负责受限上下文、材料归档及父检查点适配；大成果按next_cursor完整恢复，依赖角色取得实际artifacts，父运行停止传播，UNKNOWN不自动重试。ResearchToolTurn在独立来源会话中为子角色web_fetch启用include_file并沿用原save_source，通用turn.research赋值不覆盖来源会话。成果保存于父检查点及task_evidence，不创建独立顶层run或第二数据库。文字issues按reason保存，映射型artifacts按数组语义展开；kind、content、引用与raw_text不改写，非法类型仍失败。多值claims文本用分号连接，页码和字符定位只采用已验证摘录，其他声明标为未确认。角色计算逐项校验并保留错误索引，不把一项错误扩散为整批或改写近似结果；提示角色将result留空由程序计算。金融四角色已声明并接入v3 Recipe，映射仅在plugins/finance/research/roles.py；作者与独立核对共用material_view.py精简视图，只直接展开验证摘录、有效计算、图表及审查建议，原始角色成果沿result_ref和task_evidence完整回读，不在每次请求重复展开。历史v2只作兼容，实际部署与场景状态以股票仓库docs/计划.md为准。

## 插件前端贡献（构建登记）

通用子Agent的领域材料、工具前后处理、成果登记与摘要由当前回合策略提供的可选SubagentExtension承担；金融实现位于plugins/finance/subagents.py，Harness传输和Core执行器不判断金融v3或处理来源保存。作者与角色共用登记RoleSpec契约；evidence_ids只接受已授权task_evidence编号，source_id和artifact_ref不能替代。显式材料与结果完整分页回读；停止等待本地角色收尾，已开始且无确认回执的角色保留UNKNOWN。历史批次只读不重启执行，映射失败单列保存；新契约指纹不允许旧检查点静默切换实现。已知供应商stop与本地交付成功分开，空正文失败不自动重发。

子角色输出仅对唯一完整JSON围栏作确定性读取，围栏外说明保留于raw_text，不当作artifact；多块歧义、截断、缺字段及错误类型仍失败，不加格式修复模型调用。离线解析成功不改旧失败/UNKNOWN或替代新报告验收。

金融计算的运算枚举与输入引用格式在登记RoleSpec中维护，不能只告诉角色字段名却遗漏允许值。inputs.source使用精确source_id，摘录别名放evidence_refs；多步计算引用前项索引。校验保留原始索引与实际dependency_refs，作者选择子集或图表时补齐依赖并重排候选索引，原成果不变。非法运算、来源和失败依赖仍返回真实错误，不猜测修正或补造计算。

插件清单的 `ui.settings` 指向 `apps/web/src/plugins/<插件ID>/` 内的 TSX 入口。构建/开发/类型检查先运行 `scripts/generate-extensions.mjs`，生成已忽略的 extensions/generated.ts，不手工加宿主组件导入。面板只消费 CapabilityPanelProps 与通用 HTTP 接口，独立延迟加载；金融/搜索控制 API 和领域类型归各自前端包。新增 UI 资源需原入口构建并刷新生效，不加载任意远程 JavaScript。随源码包及已信任的本地包都经同一构建登记；未经信任的远程代码不因此获得执行或提示权限。

插件启动恢复先登记全部文件/容器提供者，再按生命周期依赖顺序恢复保存的启用意图；单个失败保留具体状态，不阻断无关能力。清单基础校验共用 harness/contracts/plugin_manifest.py，目录展示与启动登记须一致。

本地 Plugin 由原会话库 plugin_package 保存导入版本、信任确认及待部署状态；包快照在 data/private/plugin-packages。导入不执行代码。用户确认具体版本后，由唯一启动器冷启动步骤 deploy_plugins.py 部署至已忽略的 plugins/<id>，拒绝覆盖随源码包或被修改的部署目录；完整退出前不得部署。在构建确认前保留 BUILD_REQUIRED，不能把已复制当作可用。卸载只移除安装副本，保留快照和用户成果。

本地插件冷安装构建失败时，唯一启动器保留 `*.plugin-failed` 日志、恢复原包后重建，不另启执行器。包内资源在导入时校验；恢复与部署的实际验收状态见股票仓库 docs/计划.md。

性能测量使用启动器在 `data/private/runtime-processes.json` 登记的 BFF、股票启动进程、Uvicorn 服务进程和启动器 PID，按 `WorkingSet64` 读取内存；该文件只有进程元数据，不是凭据或第二份业务数据库，也不挂载给工具。不要以监听端口反推股票进程身份。

能力管理统一在工作台“能力”内按类型与 ID 展示，插件设置贡献只渲染详情内容，宿主统一卡片和启停。Skill 编辑由原 Runner 校验候选包，再由同工作区文件服务通过既有 file_edit 哈希比较与原子替换提交；不能提升 Runner 权限或另起主机文件写入后端。`-Build` 必须确保默认工具组存在，即使完整退出已移除容器；命名工作区仍按原需用恢复规则处理。

Recipe schema v2的成果更新可随原核对/发布提交，不逐工具强制填表。execution阶段选择只作诊断，模型通过output_refs明确采用产物；claims稳定ID与条件、反证、未解问题进入原交接包，不替代原文。核对阶段为发布前，不要求未来报告卡；实际交付仍在发布后验证。百分点仅在比率差值计算中产生，不允许比例水平直接换算。部署与实景以股票计划为准。

Recipe交接的outcome_version只观察阶段采用候选的正文版本；旧版本评估不得冒充当前，UNBOUND及版本不一致只提示，不阻止授权工具或发布。显式货币换算同时核验系数方向及数值。核对建议新增的事实、时点和因果仍须对照原文，不能把建议自动当作事实。

作者交接的反馈工具结果不重复携带已有Recipe、artifacts和review投影；未被claims采用的历史计算只保留摘要，采用的完整算式不改写。语义Recipe投影排除execution/active_stage_id，原始回执与完整历史仍可回读。previous_findings只保留core_answer，可选和发布后建议不自动扩展下轮任务。工具流只有在result成功解析及结果处理后才容忍尾部传输错误；结果处理失败仍按真实错误结束，不重试。

金融角色成果与工具source_excerpts读取回执分层：完整原文已保存不等于已读，读取脚本须返回可按来源指纹、页码和字符范围核验的完整JSON；裸artifacts或切片JSON不登记读取。摘录映射失败逐项保存原索引、ID及具体错误，进入作者和依赖角色材料；有效项仍可采用，原始成果不改写，不以COMPLETE掩盖局部失败。
