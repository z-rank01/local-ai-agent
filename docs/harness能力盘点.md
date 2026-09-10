# Harness 能力盘点与差距

> 记录日期：2026-09-10（2026-09-10 修订）。范围：把本仓库当作 agent harness 来评估——**具备什么、缺什么、先补什么**。
> 方法：对本仓库静态核查（源码检索 + 关键路径通读 + git 历史追溯），再与成熟 harness 对照。
> **逐工具功能面与来源索引见 [`harness能力矩阵-2026.md`](./harness能力矩阵-2026.md)**（11 个 harness × 19 个维度，142 条来源链接）。本文件只记**面向本仓库的结论**，不重复矩阵内容。

## 结论

1. **作为通用 harness 不完备。** 对照 2026 年的共识底线（Tier-0，见第四节），本仓库对齐 **1/7**，且仅有的一项是部分对齐。
2. **强项集中在数据边界与隔离**：模型可见性投影、云端门控、容器硬化——这几项**比多数成熟 harness 做得更严**。缺的不是打磨，是**扩展性 + 可观测性 + 委派**这一整层。
3. **最高优先的前置是技能结构对齐。** 本仓库的技能格式与已成标准的 **Agent Skills（`SKILL.md`）不兼容**，这直接挡住了 IN4 要接入的外部金融能力，也挡住了以后任何成熟技能的直接复用。**建议先对齐技能结构，再做 IN4.4/IN4.5 的资产迁移**（见第五节的成本修正）。

## 一、已经有的能力

| 能力 | 证据 |
| --- | --- |
| 框架无关的事件化 agent loop | `core/agent.py` 产出 `AgentEvent`，TUI/SSE 共用 |
| **agent loop 是可复用参数化类** | `Agent` 接受 `allow_tools`/`tool_tier`/`max_rounds`——**这是"能否低成本改造"的决定性判据**，本仓库得分良好 |
| 声明式工具 + 后端解耦 | 31 个 YAML 工具、5 种 backend；加静态工具不用改 Python |
| 环境变量门控注册 | `tool_registry.py:62-67`，`STOCK_BRIDGE_URL` 为空则 6 个股票工具不注册 |
| 模型可见性投影 | `agent.py:131-138` 剥离 `local_result`/`local_attachments`；云端未授权时清空 `tool_defs` |
| 容器硬化 | `read_only`、`tmpfs /tmp`、`pids_limit 128`、`cpus 2`、`mem_limit 2048m`、非 root、端口绑 127.0.0.1 |
| 进程组终止 | `sandbox.py` 超时 `killpg` 杀整棵树 |
| 两级上下文压缩 | `context_manager.py`：`micro_compact`（清工具结果）+ `auto_compact`（LLM 摘要） |
| 长工具心跳 | `tool_router.py:121-135`，每 2s 发 heartbeat |
| 额度在环反馈 | `agent.py:348-350` 把剩余联网次数回灌给模型自行规划 |
| 执行检查启发式 | `agent.py:141-148` `unfinished_execution()`，抓"说要跑却只打印代码" |
| 会话持久化 | SQLite 会话/工具调用/回复版本，支持编辑重发与重生成 |
| 审计与软删除 | `audit.jsonl`；trash 有 restore 接口 |
| 测试 | 90 个（`test_in1` 38、`test_stock_bridge` 23 等） |

## 二、共识底线：Tier-0 与 Tier-1

据矩阵归纳的 2026 年门槛。**Tier-0 = 几乎所有 harness 都有，缺了会被认为"不完整"；Tier-1 = 2026 年的分水岭。**

| Tier-0（共识底线） | 本仓库 |
| --- | --- |
| MCP 客户端 | ❌ 0 命中 |
| 自动压缩 | ✅ 两级（部分对齐：窗口写死、token 靠估算） |
| `AGENTS.md` 类指令文件 | ❌ 无（提示词都在服务端） |
| 带 auto 档的审批模型 | ❌ 只有硬 allow/deny，无"询问"档 |
| headless + JSON | ⚠️ 有 HTTP/BFF，无 `--output-format json` 式接口 |
| 可恢复会话 | ⚠️ 会话持久化有，**进行中一轮不能续** |
| token / cost 计量 | ❌ 只数调用次数 |

**Tier-0 对齐：1/7。**

| Tier-1（2026 分水岭） | 本仓库 |
| --- | --- |
| OS 级沙箱 | ⚠️ 容器层有，非 OS 级 |
| **出口管控** | ❌ 容器有网可外发——**最普遍缺失的一项，也是本仓库最大敞口** |
| hooks 生命周期 API | ❌ 无（扩展 = 写微服务） |
| 隔离上下文的子代理 | ❌ 无 |
| 非 git 检查点 | ❌ 无 |
| OTel 观测 | ❌ 无 |
| **`SKILL.md`（Agent Skills）** | ❌ **格式不兼容**，见第五节 |
| 显式 plan mode | ❌ 无（靠提示词模拟） |
| eval 体系 | ⚠️ 有手工脚本（`check_in1_tools.py`、`check_in3_model_loop.py`），未成套 |

**Tier-1 对齐：0–1/9。**

> 顺带修正两处过判：**LSP 与代码索引/RAG 都不是普遍需求**——矩阵显示 Claude Code 自身的 RAG 也只到"部分"，它靠 agentic search；Codex 明确不用 embeddings。所以"无 RAG"是**合理的设计选择**，不是缺陷。而"审批分级"虽属 Tier-0，但采用度低于 MCP/压缩，**不必排在最前**。

## 三、两个结构性观察

**1. 可靠性靠"提示词打补丁"，不是结构保证。**
`agent.py:229-235、266-271` 每轮往 system prompt 追加长段中文约束（"不要声称正在查看或执行""代码块本身不会执行"），外加 `unfinished_execution()` 正则兜底。有效，但脆弱，且每轮都要重新说服模型。成熟 harness 更倾向结构性保证（强类型工具结果、必填完成字段、独立校验步骤）。

**2. 文件预取是死代码——已查明是重构时丢掉的回归，不是刻意关闭。**

git 追溯（`git log -S` + 提交间 `git grep`）：

| 提交 | 日期 | 状态 |
| --- | --- | --- |
| `1c73143` 初始提交 | — | 在 `gateway/app.py`，**被调用**（467、585 两处） |
| `78149ad` TUI 重构 | 2026-04-20 | 移植进 `core/agent.py`，第 202 行**有调用** |
| `2a65799` feat(IN1) | 2026-09-06 | **调用点被删**，只留函数定义 |
| HEAD | — | 仅定义（`agent.py:360/367/380`），无调用 |

`78149ad → 2a65799` 对 `core/agent.py` 的 diff 只删了一行：

```diff
-        messages = await self._inject_context_into_messages(messages, session_id)
```

**结论：IN1 重构时丢掉的调用点，属历史遗留回归**（既非"从未接上"，也非"文档化的刻意关闭"——后者通常会顺手删函数并改文档，两样都没做）。

**⚠️ 别直接把那行加回去。** 同文件里相邻的两处边界都走门控——记忆 `if self.memory and not cloud:`（L216、L272）、云端工具 `if cloud and not workspace_cloud_allowed:`（L227）——**而 prefetch 辅助函数内部没有任何云端门控**。直接恢复会把本地文件正文送进云端模型，正是 IN1.4 那条边界要防的事。

**修法**：恢复调用 **+ 补云端门控** **+ 与 `workspace_cloud_allowed` 开关语义一致**；或彻底删函数并同步改 `股票能力接入.md` 的 IN1.4。**不能维持现状**——文档说它在跑，代码里它是死的。

## 四、补齐顺序

### 第 1 优先：对齐 Agent Skills 技能结构（前置 IN4.4/IN4.5）

**现状**：本仓库技能是 `/workspace/skills/*.py`，要求 `SKILL_METADATA` 字典 + `run(params)` 函数，以子进程执行，经 `skill_list`/`skill_info`/`skill_run`/`skill_register` 发现与运行。**与 Agent Skills 标准零对齐。**

**标准**（[agentskills.io](https://agentskills.io)，已有 **45+ 客户端**采用：Claude Code、Codex、Cline、Kilo、Cursor、Goose、OpenHands、Amp、Devin…）：一个技能 = 一个目录，必需 `SKILL.md`（YAML frontmatter，`name`/`description` 必填，`license`/`compatibility`/`metadata`/`allowed-tools` 可选），可选 `scripts/`、`references/`、`assets/`。核心是**渐进披露**：启动只加载元数据（约 100 token）→ 激活时加载 `SKILL.md` 正文（建议 <5000 token）→ 资源按需加载。官方提供 `skills-ref` 校验器。

**为什么这件事该排最前**：

1. **直接决定 IN4 的成本**。`finance-data` 插件的 4 个技能**恰好就是 Agent Skills 格式**（`SKILL.md` + `references/` + `scripts/`）。写了加载器，46 份方法论与 16 个脚本**按原格式直接消费**；不写，就得逐份手工改写成现有 Python 技能——本文件早前记的"迁移是改写而非拷贝"因此**需要修正**。
2. **决定以后能否复用生态**。市场上任何符合标准的技能都能直接放入，不再一次性投入。
3. **正好解决 46 份 references 的上下文问题**。渐进披露（元数据 → 正文 → 资源按需）就是为这种"一份主文件 + 大量参考文件"的场景设计的；若把 46 份塞进提示词会直接爆上下文。
4. **两者互补而非替代**。标准本身支持 `scripts/`，所以 Agent Skills 包（指令 + 脚本）与现有 Python `run()` 技能可以共存：前者承载**方法论与方法型工作流**，后者继续承载**纯计算**。

### 第 2 优先：Tier-0 廉价项

1. **token 计量 + 每模型上下文窗口** —— 现在只有调用次数预算（`CallBudget`），`context_manager.py` 窗口写死 32768、token 用 `len/2.5` 估算，**压缩阈值等于拍脑袋**。矩阵显示 token/cost 属 Tier-0。
2. **工具并行执行** —— `agent.py:313` 目前串行（`for tc in tool_calls`），改 `asyncio.gather` 即可（注意事件顺序）。
3. **重试 / 退避** —— 一次 429/5xx 现在直接结束本轮。
4. **`AGENTS.md` 指令文件** —— 现在提示词全在服务端 `gateway/prompts/modules`，用户无法按项目改。
5. **固化 eval 套件** —— 把手上的手工 agent 评测成套化。

### 第 3 优先：结构性大项

**出口管控**（当前最大安全敞口：模型侧边界严，工具侧几乎无）。矩阵显示 Claude Code / Codex / Cursor / Kilo / Aider 都有网络代理或域名白名单，而这是**最普遍缺失、也最难补**的一项——需要代理/白名单基础设施。

其后按需：MCP 客户端、子代理、检查点、hooks、plan mode。

### 目标若是"通用 harness"

MCP 与子代理绕不过，都属"难"。但本仓库架构（事件化 loop + YAML 工具表 + backend 无关 router + 可参数化 loop 类）**适合**接这两样，改造成本低于缺项清单给人的印象。

> **MCP 实现提醒**：不要照 2025 教程实现。规范已到 `2026-07-28`，是一次**破坏性无状态重构**（删除协议级 session 与 `initialize` 握手、`Mcp-Session-Id`；改为每请求在 `_meta` 自带版本与能力；新增 `server/discover`；MRTR 取代服务端发起请求）。按 2025 握手模型实现等于一开始就站在兼容路径上。细节与来源见矩阵 §2.1。

## 五、参考架构（Python）

| 项目 | 用途 | 关键点 |
| --- | --- | --- |
| **OpenHands SDK** | 最该先读的参考实现 | Action→Observation 强类型工具契约、可插拔 `SecurityAnalyzer`→确认策略、可插拔 `Condenser`、LiteLLM 供应商抽象、本地↔远程工作区可移植 |
| **`pydantic-ai-harness`** | 最快的落地路径 | 50+ 可组合能力已覆盖 planning / subagents / compaction / memory / skills / guardrails / spend limits / step persistence / OTel——**唯一明显缺口恰好是 OS 级沙箱** |
| mini-SWE-agent、SWE-ReX、Moatless Tools | 轻量对照 | mini-SWE-agent 已取代 SWE-agent |

## 六、参考基线：本机 DSH 模块清单

依赖列表本身就是一份成熟 harness 的功能面：

```
dsh-mcp-client                                   MCP 客户端
dsh-tool-subagent / -control                     子代理
dsh-tool-workflow / dsh-workflow-worker-thread   扇出编排
dsh-plan-mode                                    计划模式
dsh-tool-todo / dsh-goal / dsh-goal-round-driver 任务清单与持久目标
dsh-compaction-basic / -tool-result-pruner       压缩（含工具结果剪枝）
dsh-token-meter                                  token 计量
dsh-session-projection / dsh-session-reference   会话投影与引用
dsh-jobs-local / dsh-tool-jobs                   后台作业
dsh-pwsh-sandbox / dsh-fs-sandbox                沙箱分级
dsh-skill / dsh-skill-filesystem                 技能
dsh-tool-str-replace-editor                      精确编辑
```

对照第二节：Tier-0 七项中本仓库对齐 1 项（压缩，部分）。
