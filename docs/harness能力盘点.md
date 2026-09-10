# Harness 能力盘点与差距

> 记录日期：2026-09-10。范围：把本仓库当作 agent harness 来评估——**具备什么、缺什么、补齐难度**。
> 方法：对仓库做静态核查（源码检索 + 关键路径通读），再与成熟 harness 逐维度对照。本文件只记结论与对照表，不记过程。

## 结论

1. **作为通用 harness 不完备**；作为本金融助手的**专用 harness 接近完备**。
2. 强项集中在**数据边界与隔离**：模型可见性投影、云端门控、容器硬化。这几项比多数成熟 harness 做得更严。
3. 缺的是**扩展性 + 可观测性 + 委派**三层，属于结构性缺失，不是打磨问题。

## 一、已经有的能力

| 能力 | 证据 |
| --- | --- |
| 框架无关的事件化 agent loop | `core/agent.py` 产出 `AgentEvent`，TUI/SSE 共用 |
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

## 二、与成熟 harness 的对照

| 维度 | 本仓库 | 成熟 harness | 补做难度 |
| --- | --- | --- | --- |
| MCP 支持 | ❌ 0 命中 | 已是事实标准 | 高 |
| 子代理 / 委派 | ❌ 无 | 普遍 | 中高 |
| 计划模式 | ❌ 无（靠提示词模拟） | 多数 | 中 |
| 上下文压缩 | ✅ 两级 | 普遍 | 已有 |
| **Token 计量 / 成本** | ❌ 只数调用次数 | 普遍 | **低** |
| 编辑检查点 / 回滚 | ❌ 0 命中（trash 只覆盖删除） | 普遍 | 中 |
| 工具审批分级 | ❌ 硬 allow/deny，无"询问"档 | 普遍 | 中 |
| 沙箱隔离 | ✅ 容器层较好 | OS 级 + 容器 + worktree | 中 |
| **出口管控** | ❌ 容器有网，可外发 | 部分有代理/白名单 | **高** |
| 工具并行执行 | ❌ 串行（`agent.py:313`） | 普遍并行 | **低** |
| 重试 / 退避 | ❌ 无 | 普遍 | **低** |
| LSP / 诊断回灌 | ❌ 0 命中 | 部分 | 低 |
| Eval / 回归套件 | ⚠️ 有手工脚本，未成套 | 有成套基准 | 中 |
| Hooks / 插件生命周期 | ❌ 无（扩展=写微服务） | 普遍 | 中 |
| 项目指令文件 | ❌ 无（提示词在服务端） | `AGENTS.md` 类约定兴起 | 低 |
| 代码索引 / RAG | ❌ 无 | 常见 | 中 |
| 跨重启执行恢复 | ❌ 会话持久化有，进行中一轮不能续 | 部分 | 中 |
| git worktree 隔离 | ❌ 0 命中 | 部分 | 中 |
| 多模态输入 | ❌ 无 | 部分 | 中 |

**盘点口径**：以上"成熟 harness"参照 Claude Code / Codex CLI / Cline / Roo / Kilo / Aider / Continue / Cursor / Goose / OpenHands / Amp 的公开能力面，以及本机 DSH 的模块清单（见第五节）。

## 三、两个结构性观察

**1. 可靠性靠"提示词打补丁"，不是结构保证。**
`agent.py:229-235、266-271` 每轮往 system prompt 追加长段中文约束（"不要声称正在查看或执行""代码块本身不会执行"），外加 `unfinished_execution()` 正则兜底。有效，但脆弱，且每轮都要重新说服模型。成熟 harness 更倾向结构性保证（强类型工具结果、必填完成字段、独立校验步骤）。

**2. 文件预取是死代码，而文档把它当活的。**
`agent.py:360-438` 的 `_inject_context_into_messages` / `_prefetch_file_context` **从未被调用**（已查 `core/`、`bff/`、`tui/`、`apps/` 全部调用点）。而 `股票能力接入.md` 的 IN1.4 把"文件预取"列为需要建立云端数据边界的输入通路之一。**可能是刻意关闭（为出站边界），也可能是忘了接——两者含义完全不同，需确认意图。**
> 待办：查 git 历史确认是"刻意断开"还是"从未接上"。

## 四、建议补齐顺序

**如果目标是"本金融助手的专用 harness"**（成本/收益排序）：

1. **Token 计量 + 每模型上下文窗口** —— 现在只有调用次数预算，`context_manager.py` 的窗口写死 32768 且 token 用 `len/2.5` 估算，压缩阈值等于拍脑袋。最便宜、收益最大。
2. **工具并行执行** —— `asyncio.gather`，改动小（注意事件顺序）。
3. **重试 / 退避** —— 一次 429/5xx 目前直接结束本轮。
4. **固化 eval 套件** —— `scripts/check_in1_tools.py`、`scripts/check_in3_model_loop.py` 已是手工 agent 评测，成套化即成熟 harness 的 evals。
5. **出口管控** —— 当前最大安全敞口：模型侧边界严，工具侧几乎无（容器有网）。

**如果目标是"通用 harness"**：MCP 与子代理绕不过，且都不好补。但本仓库架构（事件化 loop、YAML 工具表、backend 无关 router）**适合**接这两样。

## 五、参考基线：本机 DSH 模块清单

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

对照第二节表格：第一档 4 项（MCP、token 计量、子代理、审批分级）本仓库 **0 项对齐**，上下文压缩为**部分对齐**。

## 附：未完成的调研

本轮曾启动一次成熟 harness 功能面对照调研（覆盖 Claude Code / Codex / Cline / Roo / Kilo / Aider / Continue / Cursor / Goose / OpenHands / Amp，以及 MCP 规范、`AGENTS.md` 类约定、SWE-bench/Terminal-Bench 等基准），抓取的资料因故未纳入本仓库，已移至仓库外暂存。若需要把第二节的"成熟 harness"一列做成逐工具矩阵，需要重新调研。
