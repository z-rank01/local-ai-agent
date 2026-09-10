# Mature AI Coding-Agent Harnesses — 2026 Capability Matrix

> **Research date: 2026-09-10.** Sources are official docs, official repos, official specs, and official changelogs unless explicitly marked otherwise. Every non-obvious claim is linked. Claims that could not be verified against an official source are collected in **§7 Unverified** and are *not* asserted in the tables.
>
> **Companion to** `docs/harness能力盘点.md` in this repo, which audited the local harness but explicitly recorded: *"本轮曾启动一次成熟 harness 功能面对照调研… 需要重新调研"* — i.e. the per-tool matrix column was missing. This document is that column.

---

## 0. Headline findings (read first)

1. **MCP is table stakes and the protocol moved twice in 2026.** The current spec revision is **`2026-07-28`**, which **removed protocol-level sessions, the `initialize` handshake, and `Mcp-Session-Id`**, making MCP stateless with per-request version/capability declaration in `_meta` ([versioning](https://modelcontextprotocol.io/specification/versioning), [changelog](https://modelcontextprotocol.io/specification/2026-07-28/changelog)). Any harness built against the 2025 handshake model is now on a *backward-compatibility* path, not the mainline.
2. **Roo Code is dead.** The extension shut down **2026-05-15**, the repo is **archived**, and the README itself says so. Successors are **ZooCode** (community fork) and **Roomote** (the original team's pivot) — **not** Kilo Code, despite Kilo's marketing. See §3.9.
3. **Aider is effectively dormant** — last PyPI release `0.86.2` on 2026-02-12, last commit 2026-05-22, last GitHub *Release* `v0.86.0` (2025-08-09), and its polyglot leaderboard has **zero 2026 entries**. No successor announcement. It remains the clearest reference for tree-sitter + PageRank repo maps.
4. **Two instruction-file standards have consolidated under the Linux Foundation's Agentic AI Foundation (AAIF):** `AGENTS.md` (**60k+ projects**, stewarded by AAIF) and **Agent Skills / `SKILL.md`** (agentskills.io, **45+ client products**). MCP itself is also an AAIF project.
5. **The 2026 differentiators are no longer "does it have tools".** They are: OS-level sandboxing as the *default* execution model (Codex, Devin), a **classifier or reviewer-subagent that replaces human approval** (Claude Code auto mode; Codex `auto_review`/guardian; Cursor Auto-review; Goose adversary mode), **hooks** as a real lifecycle API, **OTel** export, and **subagent fan-out with cost budgets** — where **Devin's managed-Devins coordinator with per-child ACU limits** is the most fully realised implementation.
6. **`pydantic-ai-harness` is the closest thing to a drop-in Python reference** for a new harness: planning, subagents, compaction, memory, skills, guardrails, spend limits, step persistence and OTel are each a composable capability. See §6.
7. **The local harness's gap is structural, not cosmetic.** Its strengths (model-visibility projection, cloud gating, container hardening) are *better than most mature harnesses*; what it lacks (MCP, delegation, hooks, token truth, egress control) is exactly the layer that every mature harness now treats as table stakes.

---

## 1. Cross-tool capability matrix

Legend: **✅** present · **◐** partial / narrow / feature-flagged · **❌** absent · **—** not verified (see §7) · **n/a** discontinued

| Dimension | Claude Code | Codex CLI | Cursor | Cline | Kilo Code | Goose | OpenHands | Aider | Continue | Amp | Devin |
|---|---|---|---|---|---|---|---|---|---|---|---|
| **1. MCP client** | ✅ tools+resources+prompts, OAuth, connectors | ✅ stdio + streamable HTTP, OAuth (HTTP only) | ✅ **tools/prompts/resources/roots/elicitation + MCP Apps** | ✅ stdio/SSE/Streamable HTTP, **tools + resources + prompts**, OAuth (**dynamic *and* pre-registered clients**) | ✅ | ✅ 70+ extensions | ✅ | ❌ none | ◐ agent mode only, **tools only** | ✅ + enterprise registry allowlist | ✅ STDIO/SSE/HTTP + marketplace + security profiles |
| **MCP server mode** | ✅ (MCP server that pushes events = "channels") | ❌ **removed** in 0.154.0 | ❌ (exposes **ACP** instead) | ❌ **no** (its `cline --acp` is an ACP *client*) | — | ✅ (MCP sampling/elicitation/roots as *client* features) | ✅ agent-server / OpenAI-compatible gateway | ❌ | ❌ | — | ✅✅ **official Devin MCP server** (sessions, playbooks, knowledge, schedules) |
| **MCP governance** | per-tool controls, org connectors, Tool Search (defer schemas) | per-server allow/deny tool lists; plugins overlay policy | **allowlist by command/URL + per-server tool allowlist + network mode** | enterprise `allowedMCPServers` + marketplace; ⚠️ **per-tool MCP auto-approve REMOVED in 4.1.16** — only a global toggle remains | ✅ | **extension allowlist (fail-closed)** | ◐ | n/a | ❌ | **MCP registry allowlist, fail-closed** | **security profiles restrict network, MCP, git, GitHub CLI** |
| **2. Subagents** | ✅ own context, built-in Explore/Plan/general; **agent teams** (experimental); **dynamic workflows** (script-orchestrated) | ✅ 2 backends, **roles**, concurrency + depth caps | ✅ built-ins Explore/Bash/Browser, custom agents, `/multitask` async, cloud VMs | ◐ **experimental, read-only** research agents | ✅ `general` + `explore`, background | — (recipes ≈ workflows, not subagents) | ✅ (compose multiple agents) | ❌ | ❌ | ✅ Search / Oracle / Librarian / Read-Thread | ✅✅ **profiles + fg/bg + managed Devins in parallel, each on its own isolated VM** |
| **Subagent cost control** | per-phase token totals in `/workflows`; effort levels | `default_subagent_model`, `reasoning_effort`, `max_concurrent_threads_per_session`, token budgets | model clamping by plan/admin | per-subagent tokens+cost rolled into task | — | — | LLM cost tracking | n/a | n/a | dial modes | ✅✅ **ACU limits per child + live ACU monitoring** |
| **3. Context mgmt** | **auto-compact** + `/compact`, micro-compact, CLAUDE.md survives compaction, auto-memory `MEMORY.md`, `.claude/rules/` | `model_auto_compact_token_limit`, custom `compact_prompt`, AGENTS.md native | auto-summarize, `/summarize` (`/compact` alias), `/context` visualiser | auto-compact | **context condensing** w/ anchored summary, provider-usage-driven trigger | context engineering, `/compact`-style | **Condenser** (pluggable: NoOp / LLM-summarising / pipeline) | `ChatSummary` via weak model, `--max-chat-history-tokens` | `/compact`; `summarize` role declared but **unused** | 432k / 500k / 1M token contexts | compaction + **Knowledge** system + DeepWiki |
| **4. Plan mode** | ✅ `/plan`, first-class permission mode, **Ultraplan** (cloud) | ✅ **collaboration mode** (separate from `update_plan`); "clear context and implement" | ✅ Plan Mode, saved plans, Shift+Tab | ✅ **Plan/Act** dual mode | ✅ custom modes incl. orchestrator | ✅ plan-before-work, planner model | ✅ conversation goals | ◐ **architect/editor** two-model split | ✅ read-only Plan mode, `cn --readonly` | ✅ deep mode | ✅ (hooks expose `exit_plan_mode`) |
| **5. Permissions** | **6 modes** (`default/acceptEdits/plan/auto/dontAsk/bypassPermissions`) + deny→ask→allow rules + **classifier auto mode** | policies `on-request`/`never`/`untrusted`/**granular**; **Starlark execpolicy**; **auto_review subagent** | 3 **Run Modes** (Auto-review/Allowlist/Run Everything) + `permissions.json` in plain English | **hybrid**: model sets a per-command `requires_approval` flag (*"Cline does not use a fixed allowlist"*) **+** deterministic `CLINE_COMMAND_PERMISSIONS` (`allow`/`deny`/`allowRedirects`); 9 auto-approve categories + YOLO (enterprise-disableable) | **allow/ask/deny + glob, last-match-wins** | tool shim, **adversary mode** reviewer | **pluggable SecurityAnalyzer** → risk levels → confirmation policy | minimal: confirmations + `--yes-always` | **allow/ask/exclude + glob + `~/.continue/permissions.yaml`** | tool-level + MCP permissions | ✅ permission scopes `Write(...)`/`Read(...)` **fed into the sandbox**; bg subagents auto-deny unapproved tools |
| **6. Sandbox / isolation** | ✅ **OS sandbox** (Seatbelt / bubblewrap+seccomp), worktree isolation `-w`, containers, cloud envs | ✅✅ **Seatbelt / Landlock+seccomp / bubblewrap / Windows tokens+job objects**; worktrees | ✅ **Seatbelt / AppArmor** via `sandbox.json` | ❌ **no OS-level sandbox** (audit is **v3.58.0, analysed 2026-02-12 — predates 4.x**, so label it); it does have git worktrees (CLI `--worktree` + Kanban). ⚠️ `CLINE_SANDBOX`/`CLINE_SANDBOX_DATA_DIR` are now in **official env tables with no semantic docs**; CHANGELOG ties sandbox sessions to *not* attaching to the shared Hub and there is a plugin-subprocess sandbox → reads as **session/state isolation, not filesystem/network confinement** | — | ❌ (local-first; allowlist instead) | ✅✅ **Docker / Apptainer / process / remote / cloud + Agent Server** | ❌ git-based only | ❌ | ✅ Orbs (server-side) | ✅✅ **OS sandbox, `--sandbox`, FAIL-CLOSED**; managed Devins on isolated VMs; Outposts self-hosted |
| **Network egress control** | ✅ sandbox network rules, first-use domain prompts, Unix-socket blocking | ✅✅ **managed HTTP+SOCKS proxy, allow/deny domains, limited mode w/ MITM, `network_access` default false** | ✅ **documented default domain allowlist (~100), 3 modes** | ❌ **not present** | — | ❌ | ◐ container-level | ❌ (deny-fragments only) | ❌ | ◐ Orbs | ✅ domain filtering via managed loopback proxy (flagged **unstable**), restricted by security profiles |
| **7. Checkpoint / undo** | ✅ snapshot per prompt, `/rewind`, restore code / conversation / both, summarize-from-here | ◐ `ghost_snapshot` = **legacy compat** + `undo` flag; backtrack UI | ✅ checkpoints (files only) + CLI `/rewind` (files **and** conversation) | ✅✅ **shadow git repo, commit per tool use, 3 restore modes**; ⚠️ **disabled in multi-root workspaces** | — | ❌ | ◐ event-sourced history | ◐ `/undo` = git revert of aider's own commits | ❌ none documented | ✅ thread forking | ✅ environment **snapshots** from blueprints |
| **8. Hooks / extensibility** | ✅✅ ~30 hook events incl. HTTP/MCP/LLM/subagent handlers; **skills**; **plugins + marketplaces** | ✅ 12 lifecycle events (Pre/PostToolUse, Pre/PostCompact, SubagentStart/Stop, …); skills; plugins + marketplaces | ✅ **~14 events incl. `preCompact`, `subagentStart/Stop`**; stdout JSON; block + **rewrite args**; plugins; skills | hooks at `.clinerules/hooks/<EventName>` (no extension; shebang + exec bit) and `.cline/hooks/<Event>.sh`; **"Enable Hooks" must be on**; `PreToolUse` supports `contextModification` + `cancel`; ⚠️ **CLI `--yolo` disables hooks** (`--act`/`--plan` keep them); plugins + marketplace; **`SKILL.md` skills** (`.claude/skills/` read natively; **global skill beats same-named project skill**); ⚠️ plugins/hooks are **SDK/CLI/Kanban only — not the VS Code/JetBrains extensions** | ✅ modes, workflows, skills, marketplace, plugins | ✅ recipes + extensions; skills (via AAIF) | ✅ **lifecycle hooks**; skills; plugins; MCP | ◐ lint/test feedback loop, `--load` | ◐ rules/prompts/MCP/hub; **no hooks** | ✅ plugins + skills + custom agents | ✅ hooks (Pre/PostToolUse, PermissionRequest, UserPromptSubmit, Stop, PostCompaction, SessionStart/End) + skills + plugins w/ marketplace |
| **9. Headless / CI** | ✅ `-p`/`--print`, `--output-format json`, `--bare`, **Agent SDK (Python/TS)** | ✅ `codex exec --json --output-schema`, SDK TS **and** Python, GitHub Action, cloud tasks | ✅ CLI `-p --force --output-format json/stream-json`, ACP, GH Actions | ✅ `cline --json` + **NDJSON**, auto-activates on pipe/redirect, `--acp`; GH Actions **recipes only, no first-party Action repo** | ✅ CLI + cloud agent | ✅ CLI + headless | ✅ REST/WebSocket + CLI + Python SDK | ✅ `--message --yes-always` | ✅ `cn -p`, `--format json`, `CONTINUE_API_KEY` | ✅ `-x/--execute`, streaming JSON, **Python + TS SDK** | ✅ CLI + Devin API + Outposts fleet orchestration API |
| **10. Resumption** | ✅ `-c`, `--resume`, **`--fork-session`**, `/branch`, sessions on disk | ✅ `resume` (`--last/--all`), **`fork`**, archive | ✅ `--resume/--continue`, session list, SDK resume | ✅ history + resume + **fork** | ✅ session history + search | ✅ sessions | ✅ event-sourced conversations; goals resumable | ◐ `--restore-chat-history` (default off) | ✅ `--resume`, `/fork` | ✅ threads, forking, remote runners | ✅ recorded/resumable **dynamic workflows**; sleep/wake child sessions |
| **11. Observability / cost** | ✅✅ **OTel metrics + traces + logs**, span tree incl. `blocked_on_user`, per-subagent spans; `/usage`, `/cost` | ✅ **OTel logs+traces+metrics** (OTLP HTTP/gRPC), token/cost events | ✅✅ **OTel export** (`cursor.token.usage`, `cursor.cost.usage`, hook/plugin events), analytics + AI-code-tracking API | ✅ **OTLP metrics + logs, but NO tracing**; + Langfuse; enterprise telemetry/audit + prompt archiving to S3/R2 | ◐ analytics/adoption dashboard | ◐ usage data | ✅ **OpenTelemetry tracing**; LLM token+cost telemetry | ◐ `/tokens` shows tokens **and** $ (no `/cost`) | ◐ `/info`; PostHog; local dev-data | ◐ pricing/threads | ◐ **ACU** accounting per session/child; usage management |
| **12. LSP / diagnostics** | ✅ **`LSP` tool**: type errors + warnings auto-reported after each edit | ◐ diagnostics/LSP crate + IDE ext exist; **auto-injection unverified** | ◐ VS Code fork so LSP exists; agent feedback **unverified** | ◐ `@problems` — **errors-only after edits, not LSP** (CHANGELOG-only) | — | ◐ tree-sitter code analysis (call graphs), not LSP | ◐ | ◐ **lint/test cmd feedback** (`--lint-cmd`, `--auto-lint`) | ❌ (`@Codebase`/context providers **deprecated**) | — | ◐ IDE integrations / Xcode MCP bridge |
| **13. Multi-model routing** | ✅ effort levels, model config, Bedrock/Vertex/Foundry gateways | ✅✅ `model_providers`, profiles, `--oss` + Ollama/LM Studio, per-role models | ✅ **Cursor Router** (Cost/Balance/Intelligence), Teams+Ent | ✅ **40+ providers** incl. Ollama/LM Studio, **per-Plan/Act-mode model** | ✅ Kilo Gateway, auto-model | ✅ 15+ providers incl. Ollama, **ACP** (use Claude/ChatGPT subscriptions) | ✅✅ **LiteLLM** 100+ providers, OpenAI Responses + Chat Completions | ✅ `--model/--editor-model/--weak-model`, Ollama | ✅ **model roles**: chat/edit/apply/embed/rerank/autocomplete/summarize | ✅ the Dial modes | ✅✅ **Adaptive** intelligent router (enterprise-gated), model allowlists |
| **14. Evals / benchmarks** | ◐ not published as a harness score | ❌ **no harness-level SWE-bench score found** | ✅✅ **CursorBench 3.2 public, score + $/task + tokens + steps** | ◐ **cline-bench** initiative (Harbor / Terminal-Bench 2.0 + Prime Intellect Environments Hub, 2025-11) — **no leaderboard or score published** | ◐ benchmarking page | ◐ | ✅✅ **SWE-bench 77.6** (README badge) | ✅ polyglot leaderboard — **stale, no 2026 entries** | ❌ none (ships **Instinct** model) | ◐ | ✅ **FrontierCode** public benchmark + leaderboard |
| **15. RAG / codebase index** | ◐ grep/glob + Tool Search; no embeddings index | ❌ **no embeddings** — ripgrep + repo map | ◐ **Instant Grep** custom engine; embeddings internals unverified | ❌ **deliberately absent** — *"No RAG. No embeddings. No vector databases."*; enterprise: *"Repositories are never indexed or cached"*; search = ripgrep + tree-sitter | ✅✅ **tree-sitter → embeddings → LanceDB/Qdrant + `semantic_search`** | ✅ tree-sitter analyse (call graphs) | ◐ | ✅✅ **tree-sitter + PageRank repo map** (the reference impl) | ❌ **deprecated** in favour of agent search + MCP | ◐ | ✅✅ **DeepWiki** auto-generated repo wikis + repo indexing (`.devin/wiki.json`) |
| **16. AGENTS.md native** | ✅ own CLAUDE.md + `.claude/rules/`, AGENTS.md support | ✅✅ **primary convention** (`project_doc_fallback_filenames`) | ✅✅ root **and nested** with precedence merge; reads CLAUDE.md too | ✅ `AGENTS.md` + `~/.agents/AGENTS.md`, `.clinerules` (+ dir), global rules, custom instructions | ✅ | ✅ (`AGENT.md`/`.goosehints`) | ✅ repo skills | ◐ **not native** — `read: AGENTS.md` in `.aider.conf.yml` | ◐ `/init` creates it; **reading unverified** | ✅ cwd + parents + subtrees + user + system-wide | ✅ **Rules & AGENTS.md** |
| **Status 2026** | active (v2.1.x) | active (0.154.0) | active (3.x) | active (**4.1.17** ext / CLI **3.0.61** / SDK 0.0.82; 67.7k★) | active (relaunched) | active (AAIF) | active (SDK v1.35.0) | ⚠️ **dormant** | active | active | active (CLI + Cloud + Desktop) |

---

## 2. Dimension-by-dimension detail

### 2.1 MCP — is it table stakes? **Yes, and it is the interoperability layer.**

- **Specification status.** Current revision **`2026-07-28`**; revisions are `Draft` / `Current` / `Final` ([versioning](https://modelcontextprotocol.io/specification/versioning)). The 2026 revision is a **breaking stateless redesign**:
  - removed protocol-level sessions and `Mcp-Session-Id`; list endpoints no longer vary per-connection;
  - removed the `initialize`/`notifications/initialized` handshake — every request carries protocol version + client capabilities in `_meta`;
  - added mandatory **`server/discover`** RPC;
  - replaced `resources/subscribe` with `subscriptions/listen`;
  - removed `ping`, `logging/setLevel`, `notifications/roots/list_changed`;
  - **MRTR** (multi-round-trip) replaces server-initiated requests; all results now carry `resultType`;
  - tasks moved out of core into an `io.modelcontextprotocol/tasks` extension.
  ([changelog 2026-07-28](https://modelcontextprotocol.io/specification/2026-07-28/changelog))
- **Prior revisions** for context: `2025-06-18` added structured tool output, made servers OAuth **Resource Servers**, required RFC 8707 Resource Indicators, added **elicitation**, resource links, `MCP-Protocol-Version` header ([changelog](https://modelcontextprotocol.io/specification/2025-06-18/changelog)). `2025-11-25` added OIDC discovery, icons, incremental scope consent, URL-mode elicitation, tool calling in sampling, and experimental tasks ([changelog](https://modelcontextprotocol.io/specification/2025-11-25/changelog)).
- **Core primitives** are resources / prompts / tools (server side) and sampling / roots / elicitation (client side) ([spec](https://modelcontextprotocol.io/specification/2025-11-25)). Under the 2026 draft these are restructured around MRTR.
- **Extensions beyond core:** **Tasks**, **Skills over MCP**, **MCP Apps** (inline UI) ([extensions overview](https://modelcontextprotocol.io/extensions/overview)).
- **Adoption is total.** Every harness surveyed except Aider is an MCP client. Distinguishing *depth*:
  - **Cursor** documents support for tools + prompts + resources + roots + elicitation + MCP Apps, OAuth with static client credentials for providers lacking DCR, and per-server network modes.
  - **Claude Code** adds **MCP Tool Search** — only tool *names* load at startup; full schemas are fetched on demand, so idle MCP servers stop eating context.
  - **Codex** supports OAuth (`codex mcp login`) but **only for streamable HTTP**; the source rejects stdio with an explicit error. Note the often-cited **`codex mcp-server` command was removed** — do not treat it as a current feature.
  - **Amp** enforces an **MCP registry allowlist that fails closed**: if the registry URL is unreachable, all MCP servers are blocked.
- **Governance is the new battleground.** Cursor (allowlist by command/URL + per-server tool allowlist), Cline (enterprise MCP server controls), Goose (extension allowlist), Amp (registry allowlist), Codex (plugins carry per-MCP-server policy overlays) all ship admin controls. `autoApprove`-style per-tool lists are the floor, not the ceiling.
- **Server mode is rarer than client mode.** Claude Code pushes events *into* a running session from an MCP server (**channels**); OpenHands exposes an Agent Server + an OpenAI-compatible gateway; Codex **removed** its server mode. Cursor and Amp expose **ACP** instead of MCP-server mode.

### 2.2 Subagents / delegation

Three distinct architectures exist, and they are not interchangeable:

| Pattern | Who | Concurrency | Intermediate results live in | Repeatable? |
|---|---|---|---|---|
| **Subagent** — child with own context returns a summary | Claude Code `Agent` tool, Cursor, Cline, Kilo, Amp, pydantic-ai-harness | limited by parent turn | parent's context (summaries) | the worker definition |
| **Agent team** — peers with own context, shared task list, peer messaging | Claude Code (experimental, `CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS=1`); Cline Agent Teams | several long-running peers | a shared task list | the team definition |
| **Workflow** — a *script* the runtime executes | Claude Code **dynamic workflows** (`/deep-research`), Amp Orbs, pydantic-ai-harness Dynamic Workflow | dozens–hundreds per run | **script variables** | **the orchestration itself** |

Claude Code's own comparison table is the clearest statement of the tradeoff ([workflows doc](https://code.claude.com/docs/en/workflows)): moving the plan into code means "Claude's context holds only the final answer" and lets the workflow *"apply a repeatable quality pattern, not just run more agents"* — e.g. adversarial cross-checking of findings before reporting.

Notable specifics:
- **Claude Code agent teams** are **disabled by default** and **cannot spawn in `-p`/SDK sessions** — a real limitation for CI. Teammates cost significantly more tokens than a single session.
- **Cursor** has the deepest shipped subagent ecosystem: three built-ins (Explore/Bash/Browser) explicitly justified by *"analysis of agent conversations where context window limits were hit"*, custom agents readable from `.cursor/agents/`, **`.claude/agents/` and `.codex/agents/`** for compatibility, `/multitask` async parallelism, cloud subagents **each on their own VM with an isolated project copy**, and subagent checkpoints that survive resume.
- **Cline subagents are deliberately crippled** in a useful way: read-only, no MCP, no browser, **no nested subagents**, and they *cannot write files*. That is a research primitive, not a worker primitive.
- **Codex** is the only one with explicit **nesting-depth** and **concurrency** caps plus per-role config files and nickname candidates.
- **Cost control** is where most harnesses are thin. Codex and Cursor document it; Claude Code shows per-phase token totals rather than enforcing caps; pydantic-ai-harness enforces `max_agent_calls`.

### 2.3 Context management

- **Two-level compaction is now the norm:** cheap tool-result pruning first, LLM summarisation second. Claude Code names these explicitly (clear older tool outputs → then summarise), and OpenHands formalises it as a pluggable **`Condenser`** interface (`NoOpCondenser` / `LLMSummarizingCondenser` / `PipelineCondenser`) driven by `should_condense()` ([condenser arch](https://docs.openhands.dev/sdk/arch/condenser)).
- **What survives compaction is the interesting design question.** Claude Code guarantees that project-root `CLAUDE.md` and auto-memory reload fresh from disk after compaction, while instructions given only in conversation may be lost ([glossary](https://code.claude.com/docs/en/glossary)). Kilo's "anchored summary" explicitly carries goal, constraints, progress, decisions, next steps and relevant files, and *updates the prior summary* rather than appending a new one.
- **Window-relative vs absolute thresholds.** Kilo checks the provider's *reported* usage after each response **and** estimates outgoing text + system prompt + tool definitions before the call, firing on whichever limit hits first. Compare the local harness's fixed 32768 constant — the mature pattern is per-model windows with live usage.
- **Memory files split into two kinds.** *User-written* instruction files (`CLAUDE.md`, `AGENTS.md`, `.cursor/rules/*.mdc`, `.clinerules`) and *agent-written* memory. Claude Code has both: `CLAUDE.md` (you write) and **auto memory** (`MEMORY.md` under `~/.claude/projects/`, first 200 lines / 25 KB loaded each session) ([glossary](https://code.claude.com/docs/en/glossary)).
- **On-demand context is the 2026 direction.** Claude Code's **MCP Tool Search** defers tool schemas; `SKILL.md` progressive disclosure loads metadata (~100 tokens) then body then bundled resources. pydantic-ai-harness makes this explicit with **Tool Search**, **Code Mode** ("the model writes one Python script that calls many tools… intermediate results never enter the context window"), and **Tool Output Limits**.
- **Prompt caching discipline is emerging as context management.** Claude Code documents *why* `/compact` costs money and why the CLAUDE.md edit doesn't apply mid-session ([prompt caching](https://code.claude.com/docs/en/prompt-caching)); pydantic-ai-harness ships `WarnOnCacheBusts` ("detect prompt-cache prefix collapses") and `SystemReminders` ("cache-safe re-injection").

### 2.4 Plan mode

Plan mode has converged on: **read-only tool surface + explicit approval gate + preserved context on exit.**
- **Claude Code** implements it as a *permission mode* (`plan`), entered by `/plan` or Shift+Tab, with `EnterPlanMode`/`ExitPlanMode` tools. Notably **sandbox auto-allow does not widen approvals in plan mode** — a fix shipped in v2.1.212.
- **Codex** is stricter than most: Plan Mode is a **collaboration mode**, and the shipped system prompt states that the `update_plan` checklist tool *"does not enter or exit Plan Mode… If you try to use `update_plan` in Plan mode, it will return an error."* Plan exit offers **"Yes, clear context and implement"** — plan→execute with a context reset as a first-class primitive.
- **Cline's Plan/Act** carries full context across the switch; Cline's docs recommend planning first because *"the planning phase intentionally builds context that Cline needs to implement changes effectively."*
- **Continue's Plan mode** publishes an unusually explicit allow/deny list of read-only actions (including MCP tools, git history, DB schema) — but note the CLI's `--readonly` allows reads **and Bash** while excluding writes.
- **Aider's architect/editor** (Sept 2024) is the ancestor of plan/edit model splitting; `--weak-model` is *not* the editor — it handles commit messages and history summarisation.

### 2.5 Permission & approval model

The industry has moved from binary allow/deny to a **three-tier + classifier** model:

1. **Deterministic rules** — ordered match (`deny → ask → allow`, first match wins in Claude Code).
2. **Auto-approve tiers** — Cline's per-category toggles; Continue's `allow`/`ask`/`exclude` with glob matchers and a persistent `~/.continue/permissions.yaml`, plus the elegant rule that **headless mode auto-excludes `ask` tools** because nobody can approve them.
3. **A model in the loop** — this is the 2026 addition:
   - Claude Code **auto mode**: a separate classifier reviews actions with **tool results stripped** so injected content can't manipulate it. It is the *default starting mode* on Pro/Max/Team.
   - Codex **`approvals_reviewer: auto_review`**: *"uses a carefully prompted subagent to gather relevant context and apply a risk-based decision framework."*
   - Cursor **Auto-review** (default since 3.6): allowlisted calls run immediately, sandboxable calls run sandboxed, the rest go to a classifier — with the honest caveat **"Auto-review is not a security boundary."**
   - Goose **adversary mode**: an independent reviewer agent sees the original task + recent messages and returns ALLOW/BLOCK; **fail-open** if the reviewer errors.

Two models worth copying:
- **Declarative policies in plain text.** Cursor's `permissions.json` takes instructions like *"Every AWS CLI command should go through approval first."* Kilo's is a glob map: `bash: {"*": ask, "git status *": allow}` with last-match-wins.
- **Codex's Starlark `execpolicy`** is the most rigorous: `prefix_rule(pattern, decision=allow|prompt|forbidden)` + `host_executable(name, paths)` to pin binaries, with **inline `match`/`not_match` tests** and a `codex execpolicy check` command. This is policy-as-code with unit tests, and it is materially stronger than shell-fragment denylists.

### 2.6 Sandboxing / isolation

Four independent layers, usually confused with each other:

| Layer | Purpose | Best-in-class |
|---|---|---|
| **OS sandbox** (syscall/namespace) | contain the process tree | **Codex** — Seatbelt (macOS), Landlock+seccomp and/or bubblewrap (Linux), restricted tokens + job objects (Windows) |
| **Container / VM** | reproducible, disposable environment | **OpenHands** — Docker, Apptainer (rootless, HPC), process, remote, cloud; plus warm-pool deferred init |
| **Git worktree** | parallel agents without clobbering | Claude Code `-w` / `isolation: worktree`; Codex `--worktree`; Cursor worktrees; Cline Kanban |
| **Network egress** | stop exfiltration | **Codex** managed HTTP+SOCKS proxy with allow/deny domains + `limited` mode with MITM; **Cursor** documented ~100-domain default allowlist across 3 modes |

Specifics worth noting:
- **Claude Code's sandbox protects Bash only** and is separate from permission rules. Default posture is **fail-open** on missing dependencies unless `sandbox.failIfUnavailable: true`. macOS needs nothing (Seatbelt); Linux/WSL2 needs `bubblewrap` + `socat` (+ optional seccomp filter for Unix-socket blocking). **Native Windows is unsupported** — WSL2 only.
- **Codex defaults `sandbox_workspace_write.network_access` to `false`**, and its Linux sandbox re-applies `.git`, resolved `gitdir:`, and `.codex` as read-only *after* layering writable roots. Codex also has an `external-sandbox` policy in its protocol enum.
- **Protected-path sets are a pattern**: `.git/config`, `.git/hooks`, `.cursorignore` (Cursor); `.git`, `.codex`, gitdir symlinks, enforced recursively (Codex).
- **Aider and Continue have no OS sandbox at all.** Aider's isolation *is* git; Continue's is Plan Mode plus permission levels.

### 2.7 Checkpointing / undo

Two implementations dominate:

1. **Shadow git repository** — Cline commits project state to a repo **separate from your history** after *each tool use*, capturing untracked files too, with per-checkpoint Compare/Restore. Default on. The tradeoff is storage and slowdown on large repos.
2. **Per-prompt file snapshots** — Claude Code snapshots files before each edit, keyed to **every user prompt**, keeps the **100 most recent** checkpoints, and persists them with the conversation so `/rewind` still works after resume. Retention defaults to ~30 days. Critically: **changes made through the Bash tool are not tracked**, and checkpoints are separate from git.

**Files-and-conversation vs files-only is the key distinction.** Cursor's IDE checkpoints revert **files only** and explicitly do not touch the conversation; Cursor's CLI `/rewind` **does** restore files and conversation state, with a conversation-only mode. Claude Code's rewind menu offers both (plus "summarize from here" / "summarize up to here", which compress conversation without touching disk).

Codex is the cautionary case: `ghost_snapshot.*` settings survive only as **compatibility shims** ("retained so legacy `ghost_snapshot` config still loads"), with undo behind a feature flag and a backtrack UI.

### 2.8 Hooks / lifecycle extensibility

**Hooks have become a real API, and the sophistication gap is large.**

- **Claude Code** — hook *event* × *matcher* × *handler*, where a handler can be a **shell command, HTTP endpoint, MCP tool, LLM prompt, or subagent**. Hooks are deterministic by design ("fire at fixed lifecycle points rather than at the model's discretion"). Task lifecycle events (`TaskCreated`, `TaskCompleted`, `TeammateIdle`) exist for agent teams, and a `PreToolUse` hook can **defer a tool call for later**, preserving its trace context across a resume.
- **Cursor** — ~14 events over stdio JSON in both directions, including **`preCompact`** and **`subagentStart`/`subagentStop`**; `preToolUse` can **block and rewrite** (`updated_input`, e.g. `npm install` → `npm ci`); matchers filter on tool name, **subagent type**, or shell regex. Distribution via project files, MDM paths, or **enterprise cloud sync every 30 minutes** with precedence `enterprise > team > project > user` — and it **reads and merges Claude Code `settings.json` hooks**.
- **Codex** — 12 events (`PreToolUse`, `PermissionRequest`, `PostToolUse`, `PreCompact`, `PostCompact`, `SessionStart/End`, `UserPromptSubmit`, `SubagentStart/Stop`, `Stop`, `Interrupt`), plus a `--dangerously-bypass-hook-trust` flag and an admin `allow_managed_hooks_only` switch. Managed-hooks-only lives in `requirements.toml` and deliberately has **no effect in `config.toml`**.
- **Skills** are now a standard, not a feature: `SKILL.md` with YAML frontmatter (`name`, `description`, optional `license`, `compatibility`, `metadata`, `allowed-tools`) and progressive disclosure ([spec](https://agentskills.io/specification)). Claude Code extends it "with invocation control and subagent execution" and treats skills as the **recommended successor to custom commands**.
- **Plugins** bundle skills + hooks + subagents + MCP servers as one installable unit, namespaced `plugin-name:skill-name`, distributed via **marketplaces** (Claude Code, Codex `codex plugin marketplace`, Cline, Cursor, Kilo). Cursor additionally participates in an open **Agent Plugins** standard (`plugin.json`).
- **Notable gap: Cline's plugins and hooks are documented as SDK / CLI / Kanban only — *not* the VS Code and JetBrains extensions.**

### 2.9 Headless / non-interactive + resumption

- The pattern is uniform: a print/exec flag, a JSON output mode, a machine-parseable stream mode, an API key env var, and a CI recipe. `claude -p`, `codex exec`, `cline --json` (auto-activates on piped stdin or redirected stdout), `cn -p`, `amp -x`, Cursor `agent -p --force`.
- **Cursor's `--force`/`--yolo` detail is easy to get wrong:** without `--force`, changes are **only proposed, not applied**.
- **`--bare` (Claude Code) is a genuinely useful CI primitive:** skip hooks, skills, commands, subagents, plugins, MCP servers, auto-memory and CLAUDE.md so the same input gives the same result on every machine.
- **Resumption and *forking* are now separate features.** Resume continues a session; fork branches it. Claude Code `--fork-session` / `/branch`, Codex `codex fork`, Continue `/fork`, Amp thread forking, OpenHands conversation goals.
- **Agent teams do not work headless.** Claude Code teammates require an interactive session; in `-p`/SDK a "named" subagent silently degrades to an ordinary subagent. Design CI paths around subagents/workflows, not teams.

### 2.10 Observability & cost

**OpenTelemetry export is the 2026 dividing line.** Claude Code, Codex, Cursor, Cline and OpenHands all export OTLP; Aider and Continue do not.

Claude Code is the most complete published trace model ([monitoring](https://code.claude.com/docs/en/monitoring-usage)):
```
claude_code.interaction                (root, one per user prompt)
├── claude_code.llm_request
├── claude_code.hook                   (detailed beta tracing)
└── claude_code.tool
    ├── claude_code.tool.blocked_on_user   ← time waiting on permission
    ├── claude_code.tool.execution
    └── (Agent tool) subagent llm_request / tool spans
```
with W3C `traceparent` propagation to the Anthropic API and to outbound HTTP MCP requests, and metrics including `claude_code.token.usage`, `claude_code.cost.usage`, `claude_code.lines_of_code.count`, `claude_code.commit.count`, `claude_code.pull_request.count`, `claude_code.code_edit_tool.decision`, `claude_code.active_time.total`.

The **`blocked_on_user` span is the single most useful idea here** for a harness builder: it separates "the model was slow" from "the human was slow", which is the number you need to justify removing an approval prompt.

Cost-model notes:
- Claude Code computes dollar figures **locally from token counts at list price**, unless an admin installs a `modelPricing` table — an estimate, not a bill.
- Cursor's OTel `cursor.cost.usage` is explicitly *"best-effort USD estimate, not an invoice"*.
- Benchmark costs are now reported per instance: the SWE-bench leaderboard carries `instance_cost` and `instance_calls`, and CursorBench reports **$/task + tokens/task + steps/task**.
- Cline Enterprise archives prompts to **S3 or Cloudflare R2** for compliance — a different answer to "observability" than tracing.

### 2.11 LSP / diagnostics

This dimension is **the least standardised and the most over-claimed.** Verified positions:
- **Claude Code has a real `LSP` tool**: "After each file edit, it automatically reports type errors and warnings so Claude can fix issues without a separate build step," plus jump-to-definition and find-references. It stays **inactive until you install a code-intelligence plugin** for the language, and **does not start plugin language servers in cloud sessions** ([tools reference](https://code.claude.com/docs/en/tools-reference)).
- **Aider** has the lint/test feedback loop: `--lint-cmd "language: cmd"` (repeatable), `--auto-lint` **default true**, `--test-cmd` / `--auto-test` default false; a non-zero exit with errors on stdout/stderr triggers a fix attempt.
- **Continue's `@Codebase` and the whole Context Providers system are deprecated**, so the often-repeated "Continue has LSP-based context providers" claim is **not supported by current official docs**; its own Edit page says *"No other additional context is provided to the model."*
- **Codex** ships a `diagnostics` crate and an IDE extension, but **no official statement that compiler/linter diagnostics are automatically injected into model context was found.**
- Cursor is a VS Code fork so diagnostics exist in-editor; **no dedicated official page describing agent-side diagnostics feedback was found.**

### 2.12 Multi-model routing

- **Role-based routing is the clearest vocabulary.** Continue names model roles explicitly: `chat`, `autocomplete`, `embed`, `rerank`, `edit`, `apply`, `summarize` (and notes `summarize` is *"not currently used"*). Aider separates main / `--editor-model` / `--weak-model`. Codex separates `model` / `review_model` / per-agent-role models.
- **Provider abstraction via a library is the Python-idiomatic answer.** OpenHands uses **LiteLLM** for "100+ providers" plus dual API support (Chat Completions `completion()` and Responses `responses()` with encrypted thinking). pydantic-ai-harness swaps providers by changing a model string.
- **Subscription auth is a 2026 feature.** Goose supports using existing Claude / ChatGPT / Gemini subscriptions via **ACP**, and Cline accepts an Anthropic Claude Code subscription or OpenAI Codex OAuth — i.e. harnesses are now credential brokers, not just API clients.
- **Automatic routing exists but is gated.** Cursor Router (Cost/Balance/Intelligence) is **Teams & Enterprise only**, and admins can *impose* Auto. Codex has `step_model_switching`; Goose documents planner+execution model splits.
- **Local models are table stakes at the low end:** Cline (Ollama / LM Studio / Atomic Chat), Continue, Cline, Goose (Ollama), Codex (`--oss --local-provider`).

### 2.13 Evals / benchmarks

- **SWE-bench Verified leaderboard (official, third-party-hosted).** Top entries as of the data snapshot: **Sonar Foundation Agent + Claude 4.5 Opus 79.2%** (2025-12-05) and **live-SWE-agent + Claude 4.5 Opus medium 79.2%** (2025-12-15); TRAE + Doubao-Seed-Code 78.8%; live-SWE-agent + Gemini 3 Pro Preview 77.4%; **mini-SWE-agent + Claude 4.5 Opus (high) 76.8%** (2026-02-17) ([leaderboard](https://www.swebench.com/)).
- **The multilingual leaderboard is now dominated by `mini-SWE-agent` as the harness**, with **Gemini 3 Flash 72.7%**, Claude 4.6 Opus 72.0%, Claude 4.5 Opus 70.7%, GLM 5 69.7% (2026-02). This is a strong signal: the *harness* has been commoditised at the top of that board, and the model is the variable.
- **OpenHands advertises SWE-bench 77.6** on its SDK README badge; the leaderboard data snapshot shows **76.8%** for Claude 4.5 Opus (high) with `mini-SWE-agent`. Treat 77.6 as the vendor's own figure.
- **Published harness-level numbers are scarce outside the SWE-bench ecosystem.** Cursor is the notable exception with a **public CursorBench 3.2** reporting score **plus** cost, tokens and steps per task. No official harness-level SWE-bench score was found for **Codex CLI**.
- **Newer benchmarks:** **Terminal-Bench 4.0** (hosted by Stanford / Harbor / Laude Institute) reports resolution rate, cost and tokens; **SWE-bench Pro** (Scale AI, 2025-09-19) has 1,865 instances across 41 repos, deliberately sourced from strong-copyleft and private codebases to resist contamination.
- **Harness vendors increasingly publish their own suite rather than a third-party number:** Cursor's **CursorBench 3.2**, Cognition's **FrontierCode**, Cline's **cline-bench** initiative (Harbor / Terminal-Bench 2.0 + Prime Intellect Environments Hub, 2025-11 — no published leaderboard). Cline publishes **no SWE-bench Verified score**, and **no harness-level SWE-bench score was found for Codex CLI** either.
- **Note the two Terminal-Bench generations in play:** Terminal-Bench **2.0** is what Cline's cline-bench targeted, while the public leaderboard is now **4.0**.
- **Eval *tooling* you can actually run:** the SWE-bench harness + `sb-cli`, **SWE-ReX** (execution backend), **SWE-smith** (synthetic task generation), **mini-SWE-agent**'s ProgramBench support, and per Cline's docs an SDK for scheduled/automated agents. Third-party reporting claims **Harbor** as the Terminal-Bench runner, and **Inspect AI** (UK AISI) is a general eval framework — *both flagged as not verified in this pass* (§7).

### 2.14 RAG / codebase indexing

- **The industry partially reversed on embeddings in 2026, and two harnesses made it explicit policy.** **Cline** states flatly: *"No RAG. No embeddings. No vector databases"*, and its enterprise docs advertise that *"Repositories are never indexed or cached"* — a security position, not a gap. **Continue deprecated** `@Codebase` and its entire Context Providers system, recommending agent file-search tools + rules + MCP instead. **Codex has no embeddings index at all** — it bundles **ripgrep** and ships a repo map. Cursor foregrounds **Instant Grep**, "a custom search engine that outperforms `ripgrep` on large codebases", with file paths encrypted before leaving the machine.
- **Where embeddings survive, they are tree-sitter-gated.** Kilo's pipeline is parse → semantic blocks (functions/classes/methods) → embeddings → vector DB (**LanceDB or Qdrant**) → a `semantic_search` tool, opt-in and off by default. **This makes Kilo the only harness of the Cline/Roo/Kilo/Codex group with a real semantic index** — Cline and Codex both deliberately have none.
- **Aider's repo map remains the reference implementation**: `grep_ast`/tree-sitter → `networkx.MultiDiGraph` of definitions/references → **personalized PageRank**, with parse trees cached in a `diskcache`/sqlite store. Token budget: default `None` → `max_input_tokens / 8`, clamped to **[1024, 4096]**, `0` disables, `--map-multiplier-no-files` default 2, `--map-refresh auto|always|files|manual`, and `--cache-prompts` silently forces `--map-refresh files`. It is per-turn re-injected, ranked by file importance — a "persistent vs transient" separation that is genuinely worth copying.
- **ZooCode adds "Semble"** on-demand semantic code search with no separate indexing workflow (vendor claim).

### 2.15 AGENTS.md / standards adoption

- **`AGENTS.md` is a de facto standard.** Official site: *"A simple, open format for guiding coding agents, used by over **60k open-source projects**."* It requires **no fields** — plain Markdown. **Nested files**: "Agents automatically read the nearest file in the directory tree… the closest one takes precedence"; the main OpenAI repo reportedly ships **88** `AGENTS.md` files. Conflict rule: closest file wins, explicit chat prompts override everything ([agents.md](https://agents.md/)).
- **Governance**: *"AGENTS.md is now stewarded by the **Agentic AI Foundation** under the **Linux Foundation**"*, having emerged from collaboration across **OpenAI Codex, Amp, Jules, Cursor and Factory**. AAIF's project list also includes **MCP**, **goose**, **Agent2Agent (A2A)**, **agentgateway** and **Agent Router** ([AAIF](https://aaif.io/)).
- **The site's advertised adopter list** includes Codex, Jules, Factory, Aider, goose, opencode, Zed, Warp, VS Code, Devin, UiPath Autopilot, Junie, Amp, Cursor, RooCode, Gemini CLI, Kilo Code, Phoenix, Semgrep, GitHub Copilot coding agent, Ona, Windsurf, Augment Code. **Note two absences/quirks: (a) Claude Code is not on that list even though its own docs describe AGENTS.md handling — Claude Code's native convention remains `CLAUDE.md`; (b) RooCode is still listed despite being discontinued.**
- **Nested/precedence implementations differ.** Cursor: root **and** subdirectories, *"more specific instructions taking precedence."* Amp: cwd + **parents up to `$HOME`** always included, **subtree files included when the agent reads a file in the subtree**, plus `$HOME/.config/amp/AGENTS.md`, `$HOME/.config/AGENTS.md`, and **system-wide** paths (`/etc/ampcode/`, `/Library/Application Support/ampcode/`, `%ProgramData%\ampcode\`).
- **`SKILL.md` is the second standard and is younger but broader.** Format spec at [agentskills.io/specification](https://agentskills.io/specification); frontmatter `name` (≤64 chars, lowercase + hyphens, must match directory), `description` (≤1024), optional `license`, `compatibility`, `metadata`, `allowed-tools` (experimental). **45+ client products** list support, including Claude Code, ChatGPT & Codex, Cursor, Amp, Goose, OpenHands, Gemini CLI, GitHub Copilot, VS Code, JetBrains Junie, Kiro, Qodo, Tabnine, TRAE, Pulumi Neo, Letta, OpenCode, Factory, Mistral Vibe, Spring AI, Databricks Genie Code, Snowflake Cortex Code, and the Python frameworks **fast-agent** and **pydantic-ai-harness**. (Roo Code is still listed there too.)
- **So there are three converging standards, all under AAIF/LF:** `AGENTS.md` (instructions), `SKILL.md` (procedural knowledge), **MCP** (tool/context transport). Plus **ACP** (Agent Client Protocol) for *UI-to-agent* attachment, implemented by Cline, Cursor, Goose and Zed.

---

## 3. Per-harness distinguishing details

### 3.1 Claude Code (Anthropic)
~50 built-in tools. Standouts: **`Agent`** (subagents), **`Workflow`** (dynamic workflow runtime), **`EnterPlanMode`/`ExitPlanMode`**, **`EnterWorktree`/`ExitWorktree`**, **`LSP`**, **`Monitor`** (background command/WebSocket whose output lines are fed into the live conversation), **`CronCreate`/`ScheduleWakeup`** (self-paced `/loop`), **`SendMessage`/`ListAgents`** (cross-session messaging), **`ToolSearch`**, **`TaskCreate`/`TaskGet`/`TaskList`/`TaskUpdate`** (which superseded `TodoWrite`), and **`ReportFindings`** (structured review findings with category slugs). Six permission modes; hooks whose handlers can be subagents. **Surfaces**: CLI, VS Code, JetBrains, Desktop, web, mobile via Dispatch, Slack. `/teleport` pulls a cloud session into the terminal; `--cloud` pushes a local task to the web.

### 3.2 OpenAI Codex CLI
**Platform-native sandboxing is the differentiator** (Seatbelt / Landlock+seccomp / bubblewrap / Windows restricted tokens+job objects), plus the **Starlark `execpolicy`** engine and the **`auto_review` reviewer subagent**. Also: `codex exec` with JSONL + `--output-schema`, `codex fork`, `--worktree`, `codex plugin marketplace`, an `external-agent-migration` crate that imports MCP servers, hooks, skills, subagents and AGENTS.md **from other agents**, and a `requirements.toml` managed-policy layer.
**Doc-drift warning:** widely mirrored docs still describe `--full-auto`, `--yolo` (as a normal flag) and **`codex mcp-server`**. In 0.154.0 `--full-auto` **does not exist** (replaced by `--approve-for-me`), `--yolo` is only a hidden alias of `--dangerously-bypass-approvals-and-sandbox`, `codex mcp-server` **was removed**, and `--ask-for-approval` accepts **only `on-request` and `never`**.

### 3.3 Cursor
The deepest *breadth* of the surveyed tools: MCP (all five client primitives + Apps), hooks with argument rewriting, first-class subagents incl. cloud VMs, `permissions.json` + `sandbox.json` + 3 Run Modes, documented ~100-domain egress allowlist, worktrees, nested AGENTS.md, plugins + skills, OTel export, Cursor Router, and the only **current, cost-aware public benchmark** (CursorBench 3.2). Reads `.cursor/agents/`, `.claude/agents/` and `.codex/agents/`, and merges Claude Code `settings.json` hooks.
*Unconfirmed by official sources:* an xAI/SpaceX acquisition (press only). What **is** official is a **Grok Bot** product surface with `cursor.grok_bot.*` OTel events and an Enterprise "Action Recording" toggle.

### 3.4 Cline
Surfaces (2026): VS Code extension (`saoudrizwan.claude-dev`), **Cline CLI** (TUI + NDJSON headless + hub daemon + schedules + connectors + `--worktree` + `--acp`), Desktop (Tauri+Bun+Next.js), SDK, and Kanban (`npx kanban`). Versions: **4.1.17** ext / CLI **3.0.61** / SDK 0.0.82; Apache-2.0, 67.7k★. Note the JetBrains plugin is **not open source** ("Currently we are not open-sourcing JetBrains plugins").

Broad in a distinct direction: **ACP** support (works as the agent in Zed, JetBrains, Neovim, Emacs, Xcode), a **hub-spoke SDK** (`@cline/core` ClineCore = "the full Cline harness") with process isolation and a local daemon, **Kanban** for parallel agents in isolated worktrees, **cron-scheduled agents**, chat connectors (Telegram/Slack/Discord/Google Chat/WhatsApp), and a complete enterprise story (WorkOS SSO, RBAC, org-wide provider pinning, MCP allowlisting, YOLO disablement, OTel metrics+logs, **prompt archiving to S3/R2**, REST API). ClinePass is $9.99/mo.

**Cline has the clearest and most deliberate *absences* of the surveyed tools:**
- **No RAG by design.** Official blog: *"No RAG. No embeddings. No vector databases."* Enterprise docs: *"No codebase indexing — Repositories are never indexed or cached."* Search is ripgrep + tree-sitter. This is a **security/architecture position**, not a backlog item.
- **No OS sandbox** and **no network egress control**. ⚠️ *Caveat:* the "no sandbox" finding rests on an **independent third-party audit** (agent-safehouse.dev, v3.58.0, 2026-02-12), not Cline docs — while **undocumented `CLINE_SANDBOX` / `CLINE_SANDBOX_DATA_DIR` env vars do appear in 4.x**. Treat as **unresolved**, and do not print "Cline has no sandbox" as settled fact.
- **No MCP server mode** (its `cline --acp` is an ACP *client*), no `/memory`, no `/status`.

**Two precision notes that are repeatedly got wrong:**
- **Memory Bank is a methodology, not a built-in feature** — a set of markdown files you enable by pasting custom instructions; its triggers are natural language ("update memory bank"), not commands.
- **Cline's permission model is a model-decided hybrid, and describing either half alone is wrong.** Official docs: *"Cline does not use a fixed allowlist. The model marks each command with a `requires_approval` flag."* Separately there is a **deterministic** environment control, `CLINE_COMMAND_PERMISSIONS` (`allow`/`deny`), where **deny overrides allow** and **if `allow` is set, non-matching commands are denied** — i.e. it can become a true allowlist, but only opt-in.
- **Per-tool MCP auto-approve was REMOVED in 4.1.16** — the changelog calls such entries *"no-ops that implied granularity the approval path does not have."* Only the global "Use MCP servers" toggle remains. **Auto-approve has nine categories** total (the seven core ones plus "Use MCP servers" and "Enable notifications").
- **The documented delegation tool is `use_subagents`.** There is **no `new_task` tool**; that historical concept now surfaces as the **`/newtask` slash command**. Listing `new_task` as Cline's subagent mechanism is a common error.
- **Product naming:** the shipped CLI is **Cline CLI (3.0.61)**; **no official Cline page uses the string "CLI 2.0".**

**Diagnostics yes, LSP no.** `@problems` plus **errors-only** auto-inclusion **after file edits** (warnings are deliberately excluded). **No LSP client or protocol integration exists** — the only LSP-adjacent artifact, `cline/typescript-lsp-plugin`, uses the **TypeScript Language Service API, not an LSP server**.

**Checkpoints** use a **shadow git repo** with three modes — **Restore Files** (files only, keep chat), **Restore Task Only** (delete messages, keep files), **Restore Files & Task** — and now **refuse to run if commits were made after the checkpoint** (a no-clobber guard). They are **disabled in multi-root workspaces**. **OTel covers metrics and logs but tracing is explicitly not implemented** (*"❌ Distributed tracing (not yet implemented) ❌ Custom instrumentation API ❌ Sampling configuration"*), plus Langfuse spans in release builds. Plugins/hooks are **SDK/CLI/Kanban only, not the VS Code or JetBrains extensions**, and **`--yolo` disables hooks**.
**Deprecated / date-qualified in 2026:** **Focus Chain** (*"No direct replacement"*), **`.clineignore`** (*"removing `.clineignore` as a supported feature"* — its replacement hook guard *"should not be treated as a security boundary"*), **"Explain Changes"**, and per-tool MCP auto-approve checkboxes. **`.clinerules/workflows/` is hidden from the Customize menu and absent from the current sitemap/llms.txt** — CHANGELOG + audit evidence only, so **date-qualify it**.
**Newer surface:** **Kanban is a "research preview"** and **cross-runtime** (*"works with Cline CLI, Claude Code, Codex, OpenCode"*). **Schedules can trigger on external events** (webhooks, GitHub events, plugin signals) in addition to cron, with dedup/filtering/retry. **ACP `--auto-approve` defaults to `false`** (*"Nothing is auto-approved by default"*) vs. `true` elsewhere. **Agent Plugins v1** anti-supply-chain default, verbatim: *"Automatic discovery intentionally does not scan workspace `.agents/plugins` directories, so opening a repository does not implicitly activate repository-controlled MCP servers."*
**Enterprise governance** (deepest of the surveyed group): WorkOS AuthKit SSO, three-tier RBAC (Member/Admin/Owner), prompt storage to S3/R2, `yoloModeAllowed`, and MCP allowlisting with **four documented modes** — `mcpMarketplaceEnabled:false`, `allowedMCPServers`, `remoteMCPServers` with `alwaysEnabled`, and `blockPersonalRemoteMCPServers` to *"prevent shadow IT"*.
**Evals:** the **cline-bench** initiative (Saoud Rizwan, 2025-11-20) uses **Harbor / Terminal-Bench 2.0** + Prime Intellect Environments Hub, **open-source repos only**, with **no results table or leaderboard published**; **no Cline-published SWE-bench Verified score**. "Cline Bench"/"Context-Bench" appear **only in a third-party post — do not print**.
⚠️ **Do not print any Cline funding or acquisition claim.** No official Cline source states a funding round, acquisition, or governance/foundation change. The "Cline Bot Inc" entity and the SDK-backed architecture migration are verifiable; **corporate finance is not**.

### 3.5 Kilo Code
Relaunched in 2026 (the legacy VS Code/JetBrains extension **reached EOL 2026-07-31**; the current product is `Kilo-Org/kilocode`, license changed Apache-2.0 → **MIT**). Best-in-class items: a genuinely **declarative `allow`/`ask`/`deny` permission map with globs and last-match-wins**, applied to **MCP tools as well as built-ins**; first-class custom subagents (`general` full-access, `explore` read-only) invocable via a `task` tool or `@agent-name`, with **`background: true`** and a non-interactive child contract; **context condensing** driven by provider-reported usage with a *re-anchored* summary; and an opt-in **tree-sitter + embeddings + LanceDB/Qdrant** semantic index.
*Not verified:* that Kilo is Roo Code's designated successor (Roo's own README names **ZooCode** and **Cline**), and whether the current repo is a fork or an independent rewrite.

### 3.6 Goose (Block → AAIF, Apache-2.0, Rust)
The most **security-forward open-source agent**. Unique features: **adversary mode** (independent reviewer agent over tool calls, fail-open on error, configured by a plain-language `adversary.md`), **prompt-injection detection** with a self-hostable Classification API, an **extension allowlist** fetched from a URL (fail-closed on non-match), and **MCP sampling / elicitation / roots / Apps** support. Broadest provider story via **ACP** (reuse Claude/ChatGPT/Gemini subscriptions) and 15+ providers incl. Ollama. Recipes (reusable, parameterised, cron-schedulable task templates) and a tree-sitter-based **codebase analysis** extension with call graphs. Desktop + CLI + API.

### 3.7 OpenHands (formerly OpenDevin)
**The best reference architecture for a Python harness** (§6). The SDK is a ground-up redesign with a paper ([arXiv 2511.03690](https://arxiv.org/abs/2511.03690)) claiming it *"uniquely integrates native sandboxed execution, lifecycle control, model-agnostic multi-LLM routing, and built-in security analysis"* versus the OpenAI/Claude/Google SDKs, and reports that V1 *"substantially reduces system-attributable failures over V0 with negligible event-sourcing overhead."* Ships four sandbox providers (Docker, Apptainer for rootless HPC, local process, remote), a warm-pool **deferred init** (`POST /api/init`) for pre-warmed pods, an OpenAI-compatible gateway in front of the agent server, **Critic** (experimental LLM-based real-time action evaluation), persistent two-tier memory, plugins, hooks, skills, and OTel tracing to Laminar/MLflow/Honeycomb/any OTLP backend. Actively released (SDK up to v1.35.0).

### 3.8 Aider — dormant, still instructive
No MCP, no subagents, no hooks, no LSP, no sandbox, no AGENTS.md. What it still teaches: the **tree-sitter + PageRank repo map** with an exactly-specified token budget; **git-as-checkpointing** (`--auto-commits` and `--dirty-commits` both default true, `/undo` refuses to revert commits Aider didn't make); the **architect/editor two-model split**; and the lint/test feedback loop. Its polyglot leaderboard (225 Exercism exercises, C++, Go, Java, JS, Python, Rust) is **stale — no 2026 entries**.

### 3.9 Roo Code — **discontinued**
Repo `RooCodeInc/Roo-Code` is **archived**; last push 2026-05-15, ~24.3k stars, Apache-2.0. The README states verbatim: **"The Roo Code Extension was shut down on May 15th."** It redirects to **ZooCode** ([Zoo-Code-Org/Zoo-Code](https://github.com/Zoo-Code-Org/Zoo-Code), "a fork started by the Roo Code community", v3.82.0, adds Semble semantic search, stronger orchestrator workflows, and a **Destructive Command Guard**) and **Cline**. The original team pivoted to a cloud product, **Roomote**.
The final README's feature list is **reduced to "Modes: Code, Architect, Ask, Debug, and Custom Modes"** — **Orchestrator mode and Boomerang Tasks are no longer listed**. Historically Roo contributed `.roorules` / `.roo/rules/`, `new_task` delegation with isolated subtask context, and Qdrant-based indexing; those are **historical claims I could not re-verify** against live official docs (`docs.roocode.com/llms.txt` 404s).
**Kilo markets itself as the migration target and publishes a "Roo Code Shut Down" comparison page — that is vendor marketing, not governance.**

### 3.10 Amp (Sourcegraph)
Modes-and-models abstraction: **"The Dial"** with four built-in modes (`low`, `medium`, `high`, `ultra`), each combining a model, reasoning effort, system prompt, tools and an oracle. **Specialist subagents** Search / **Oracle** (hard reasoning & planning) / **Librarian** (external codebases) / Read Thread, each with its own context window — but explicitly **isolated: they cannot talk to each other, you cannot guide them mid-task, and the main agent receives only the final summary.** Deep MCP governance (registry allowlist, fail-closed, package-name-level matching with explicit blocking of `uvx --with`, alternate indexes, direct URLs and local paths). **Orbs** = server-side execution (`-ox`), automations, multiplayer, portals. `-x/--execute` headless + streaming JSON, **Python and TypeScript SDKs**, remote runners, thread forking, plugins + skills, AGENTS.md across cwd/parents/subtrees/user/system-wide.

### 3.11 Devin / Cognition — the most capable delegation model of any surveyed harness

Cognition now ships three surfaces: **Devin Cloud**, **Devin for Terminal (CLI)**, and **Devin Desktop (formerly Windsurf)** — the earlier Windsurf product line is now a Devin surface. Its capabilities are documented at a level of detail that rivals Claude Code:

- **OS-level sandbox with fail-closed semantics — the strongest posture in this survey.** `--sandbox` derives writable paths from granted `Write(...)` scopes plus the workspace; everything else is read-only; paths covered by `Read(...)` deny rules are **hidden from sandboxed commands entirely**. Two sharp details: *"If sandbox resolution fails… the CLI will **refuse to start** rather than running unsandboxed"* (explicitly contrasted against fail-open designs), and **`Write(...)` scopes granted mid-session dynamically expand the sandbox for subsequent commands**, while mid-session `Read(...)` approvals cannot reveal a path hidden by a deny rule — *"which stays hidden for the whole session."* Requires `bubblewrap` + `socat` on Linux; **not supported on Windows** (hard-fails, including when running as an ACP server inside an IDE). Domain-level network filtering runs through a managed loopback proxy, and is **explicitly flagged unstable**.
- **Three tiers of delegation, all cost-accounted.** (1) **Subagents** with independent conversation chains, chosen from **subagent profiles**, running **foreground** (parent pauses) or **background** (parallel; *"Unapproved tools are automatically denied"*). Devin claims *"subagents both improve overall coding performance and reduce cost."* (2) **Managed Devins** — a coordinator session spawns child sessions **each in its own isolated VM**, and can message them, monitor **ACU consumption**, sleep/terminate them, and **schedule messages to itself** to check back later. Sessions are proposed for approval before launch. (3) **Dynamic workflows** — *"a recorded, resumable script"* for fan-out/combine patterns, i.e. the same third-tier architecture as Claude Code workflows.
- **Model routing as a product decision.** **Adaptive** is an intelligent router that picks a model per request, billed at a **fixed per-token rate regardless of which underlying model is chosen** (self-serve: $0.50/M input, $2.00/M output, $0.10/M cache read) — an unusual and interesting pricing model that makes routing transparent to the customer. **Disabled by default for enterprise** until an admin enables it.
- **Hooks:** 6 events — `PreToolUse`, `PostToolUse`, `PermissionRequest`, `UserPromptSubmit`, `Stop`, `PostCompaction`, `SessionStart`, `SessionEnd` — with matchers over tool names (`read`/`write`/`edit`/`apply_patch`/`exec`/`grep`/`glob`/`skill`/`todo_write`/`exit_plan_mode`, and `mcp__github__create_issue` style for MCP tools).
- **Both sides of MCP:** a client (STDIO/SSE/HTTP + marketplace) **and an official Devin MCP server** exposing session management, playbooks, knowledge and scheduling to *other* agents — so Devin can be driven by Claude Code, Codex, or any MCP client. There is also a documented **`/handoff`** that moves a task from the Devin CLI to a cloud session, **from Claude Code, Codex, or any coding agent**.
- **Config import from rivals:** Devin CLI can import settings from **Cursor, Windsurf, Claude Code, GitHub Copilot, OpenCode and Zed**.
- **Security Profiles** restrict network, MCP, git and GitHub CLI access, bound to orgs/automations/sessions. **Devin Outposts** runs sessions on your own infrastructure with self-hosted workers, a fleet API and an orchestration guide.
- **RAG:** **DeepWiki** auto-generates architecture diagrams, documentation and source links per repo, configurable via `.devin/wiki.json` — a knowledge-artifact approach rather than an embedding index.
- **Own benchmark:** **FrontierCode**, Cognition's public coding benchmark and leaderboard.

### 3.12 Still missing from this matrix

**GitHub Copilot coding agent / Copilot CLI, Google Jules, Factory, Qodo, Warp, Onyx and other 2026 entrants were not researched.** A delegated stream covering them was stopped before reporting. Treat their absence as a **gap in this document, not as evidence of weak capability.** Artifacts fetched before the stop are preserved at `harness-matrix-2026/raw/commercial/` and `raw-agent/`.

---

## 3b. What this matrix already contradicts

Widely-repeated 2026 claims that the primary sources do not support:

1. **"Kilo Code is Roo Code's successor."** Roo's own shutdown README names **ZooCode** and **Cline** only. Kilo publishes a migration guide and a "Roo Code Shut Down" comparison page — marketing, not governance.
2. **"Continue.dev has LSP-based context providers / codebase RAG."** `@Codebase` and the entire Context Providers system are **deprecated** in Continue's own docs, which now recommend agent file-search tools + MCP. Its Edit docs state *"No other additional context is provided to the model."*
3. **"Codex has `--full-auto`, `--yolo`, and `codex mcp-server`."** None are current in 0.154.0: `--full-auto` does not exist (replaced by `--approve-for-me`), `--yolo` is only a hidden alias of `--dangerously-bypass-approvals-and-sandbox`, and the MCP server mode was removed.
4. **"Aider has `/cost`."** It does not; `/tokens` prints tokens **and** dollar cost.
5. **"Cline lets you auto-approve individual MCP tools."** Per-tool MCP auto-approve was **removed in 4.1.16** as *"no-ops that implied granularity the approval path does not have."* Only the global "Use MCP servers" toggle remains.
6. **"Cline uses an allowlist (or a denylist) for commands."** It uses **both mechanisms and neither alone**: the model sets a per-command `requires_approval` flag, *and* a separate deterministic `CLINE_COMMAND_PERMISSIONS` env var provides `allow`/`deny` (deny overrides allow; setting `allow` makes it a true allowlist).
7. **"Cline's subagent mechanism is `new_task`."** The documented delegation tool is **`use_subagents`**; `/newtask` is a slash command, and no `new_task` tool exists.
8. **"Cline CLI 2.0."** No official Cline page uses that string; the shipped CLI is **3.0.61**.
9. **"Cline has LSP integration."** It has **diagnostics without LSP** — `@problems` with **errors-only** feedback after edits, and no LSP client or protocol integration.
10. **"Cline has an OS-level sandbox."** Unresolved, not established: an audit found none (v3.58.0, **predating the 4.x line**), while `CLINE_SANDBOX`/`CLINE_SANDBOX_DATA_DIR` sit in official env tables **without semantic documentation**. Current reading is **session/state isolation, not filesystem/network confinement** — do not print either direction as settled.
11. **"Cline raised/was acquired."** **No official Cline source** states any funding round, acquisition, or governance/foundation change. The "Cline Bot Inc" entity and the SDK architecture migration are verifiable; corporate finance is not.

---

## 4. The emerging consensus minimum for 2026

A harness is **incomplete without** the following. These are not aspirational — every listed item is shipped by a majority of the surveyed mature harnesses, and several are shipped by *all* of them.

**Tier 0 — non-negotiable (present in essentially every mature harness):**
1. **MCP client** supporting at least stdio + streamable HTTP, tools, and per-server enable/disable. Aider is the only surveyed harness without it, and Aider is dormant.
2. **Auto-compaction** — two-level (cheap tool-result pruning, then LLM summarisation) with a per-model window.
3. **An instruction file** — `AGENTS.md` natively, or a documented equivalent with an AGENTS.md bridge.
4. **Some approval model** with an auto-approve or bypass tier.
5. **Headless mode** with structured (JSON) output.
6. **Session resumption.**
7. **Token accounting with a cost figure** — even if it is an estimate.

**Tier 1 — the 2026 bar (what separates "mature" from "toy"):**
8. **OS-level sandboxing as a default execution mode**, not an opt-in plugin. Codex, Claude Code and Cursor all ship it; OpenHands substitutes containers.
9. **Network egress control.** Codex and Cursor document explicit allow/deny domain policy; this is the item most commonly missing.
10. **Hooks** as a lifecycle API with at least pre/post tool use.
11. **Subagents with isolated context**, and ideally a script/workflow tier for fan-out that does not consume the main context.
12. **Checkpointing/undo** that is *not* just git — either a shadow repo or per-prompt snapshots.
13. **OTel export** (traces + token/cost metrics), which is now the expected enterprise integration.
14. **Skills (`SKILL.md`)** — the second standard, already at 45+ clients.
15. **Explicit plan mode** as a read-only phase with an approval gate.
16. **A published eval story**, or at minimum a regression suite.

**Tier 2 — state of the art (where leaders differentiate):**
17. A **classifier or reviewer-subagent in the approval loop** (Claude Code auto mode, Codex `auto_review`, Cursor Auto-review, Goose adversary mode).
18. **Policy-as-code with tests** (Codex Starlark `execpolicy` with inline `match`/`not_match`).
19. **Per-subagent cost/model budgets** and nesting/concurrency caps — Devin's per-child **ACU limits + live ACU monitoring** is the reference.
20. **Worktree or VM isolation** for parallel agents (Devin gives each managed child its **own VM**).
21. **On-demand context** (tool search, progressive-disclosure skills, code-execution tool batching) rather than putting everything in the prompt.
22. **Cache-aware context management** (avoiding prefix collapse).
23. **Durable execution** across restarts (Temporal/DBOS/Prefect-style); Devin's *"recorded, resumable"* dynamic workflows.
24. **Observability of the *human* wait** — Claude Code's `blocked_on_user` span.
25. **Fail-closed security posture** — if the sandbox can't start, refuse to run. Devin is the only surveyed harness that does this by default; Claude Code is explicitly **fail-open** unless `sandbox.failIfUnavailable: true`.
26. **Sandbox scope driven by the permission model** rather than configured separately — Devin derives writable paths from `Write(...)` grants and enforces `Read(...)` denials at the OS level.

**Local harness verdict against this list:** Tier 0 = **1/7** (has compaction only; and even that is partial — fixed window, estimated tokens). Tier 1 = **0-1/9**. Tier 2 = 0. This matches `harness能力盘点.md`'s own conclusion that the gap is structural rather than cosmetic.

---

## 5. Hard to retrofit vs. easy add-ons

The right axis is not "how much code" but **"does the change reach into the loop's invariants and the trust model."**

### 5.1 Genuinely hard (architecture-level; touches invariants or trust)

| Capability | Why it's hard | Evidence |
|---|---|---|
| **MCP** | Not a feature but a protocol boundary: transport lifecycle, capability negotiation, schema→tool-definition translation, auth/OAuth, per-server trust and allowlisting, and — post-2026 revision — statelessness with per-request version metadata. Retrofitting it forces your tool abstraction to become *dynamic* (tools discovered at runtime) rather than a fixed registry. | Claude Code needs a separate `ToolSearch`/`WaitForMcpServers` mechanism precisely because idle MCP servers bloat context; Cursor needed per-server *network* policy |
| **OS-level sandboxing** | Requires per-platform syscall/namespace work (Seatbelt, Landlock+seccomp, bubblewrap, Windows job objects), a dependency-availability story, a **fail-open vs fail-closed** policy decision, and a redesign of the permission prompt flow so sandboxed commands can be auto-approved. It also **changes what "approval" means**, so it is entangled with the permission engine. | Claude Code: separate `sandbox.failIfUnavailable`, WSL2-only on Windows, seccomp filter optional; Codex: three OS implementations plus protected-path re-application *after* writable-root layering |
| **Network egress control** | Needs an in-process proxy (HTTP **and** SOCKS), CA/MITM handling for `limited` mode, per-domain allow/deny policy, and careful interaction with the sandbox — plus a decision about local/private IP binding. Must be wired into the tool layer, not bolted on. | Codex `network-proxy` binds 127.0.0.1:3128 + :8081, auto-enables MITM in limited mode, and blocks hostnames that *resolve* to private IPs even if allowlisted |
| **Subagents / delegation** | Requires: a second agent loop with its own context, a result-summarisation contract, tool-scope restriction per child, concurrency control, cost attribution, and — if you want workflows — moving orchestration **out of the model** into an executable script with its own state. Isolation and cost accounting are the parts people underestimate. | Claude Code needed three separate mechanisms (subagents, agent teams, workflows) with different state locations; Amp's subagents are isolated from each other *by design* |
| **Checkpointing beyond git** | Requires snapshotting before **every** edit, storage/retention policy, interaction with untracked files, and a decision about whether a restore also rewinds *conversation* — plus the honest limitation that Bash-driven mutations aren't captured. | Claude Code caps at 100 checkpoints and 30-day retention, and documents that Bash changes are untracked; Cline's shadow repo trades storage/speed on large repos |
| **Hooks with real power** | A hook API that can *block* and *rewrite* tool arguments forces a refactor of the tool-dispatch path into a pipeline with veto/transform semantics, plus trust management (who may define hooks) and optional deferral with trace-context preservation. | Cursor's `preToolUse` returns `permission: allow\|deny` **and `updated_input`**; Claude Code's deferred `PreToolUse` must save and rejoin trace context across a resume |
| **True token accounting** | Needs a real tokenizer, per-model windows and pricing, cache read/write accounting (a large and easily-misreported term), and propagation through compaction and subagents. Replacing a `len/2.5` estimate changes every threshold in the system. | Claude Code reports cache read/write separately in `/usage` and warns that the local dollar figure is an estimate; Cursor's OTel cost is explicitly "not an invoice" |
| **Durable resumption mid-turn** | Persisting a *completed* session is easy; resuming an **in-flight** turn requires event sourcing or step persistence, idempotent tool replay or explicit "unknown outcome" handling. | OpenHands is event-sourced with explicit claims about low overhead; pydantic-ai-harness ships Step Persistence + Temporal/DBOS/Prefect backends; Codex's exec-server forwarder *"does not replay requests or persist execution state"* |

### 5.2 Genuinely easy (add-ons; localised, low blast radius)

| Capability | Why it's easy |
|---|---|
| **`AGENTS.md` support** | Read a file at session start and inject it. The spec has *no required fields*. Explicitly the lowest-effort item on this list. |
| **Tool parallelism** | `asyncio.gather` over independent tool calls; the only real work is preserving deterministic event ordering. |
| **Retry / backoff** | Wrap the provider call. |
| **Per-model context windows + pricing table** | Configuration plus a lookup; the *hard* part is the tokenizer, which is a library dependency. |
| **Plan mode** | Add a read-only tool subset and an approval gate; you likely already have tool scoping. |
| **Skills (`SKILL.md`)** | Load markdown with YAML frontmatter on demand; progressive disclosure is just staged injection. |
| **Checkpointing via git worktrees** | `git worktree add` plus a per-session branch. |
| **Memory files** | Read/write markdown under a per-repo directory with a size cap. |
| **Headless + JSON output** | Serialise the event stream you already emit. |
| **Cost *display*** | Multiply the token count you already have by a price table. |
| **Subagent *spawning*** (the plumbing) | A tool that constructs a second loop with a restricted tool set — easy **if** your loop is already a reusable class taking a tool subset, and hard if it isn't. |

### 5.3 The deciding factor: is your loop reusable?

The single best predictor of retrofit cost is whether the agent loop is a **reusable, parameterisable class** rather than an inline function.

- **Aider and OpenHands made the loop reusable early and got subagents/workflows cheaply.**
- The local harness is **already in a good position**: `core/agent.py` is a class emitting structured `AgentEvent`s, tools are declared in YAML with backend dispatch, and `Agent.__init__` already accepts `allow_tools`, `tool_tier`, and `max_rounds` — so a subagent is *mostly* "construct another `Agent` with a narrower tool list and a fresh message list."
- What it does **not** have is the part that makes subagents *safe and cheap*: no per-subagent token accounting, no nesting/concurrency limits, and a single shared `ContextManager` whose window is a global constant.

**Recommended ordering for the local harness** (cost/benefit, consistent with its own §4):

1. **Token truth** — real tokenizer, per-model window and pricing, cache-aware. Unblocks correct compaction thresholds and makes everything downstream measurable. Lowest effort, highest leverage.
2. **Tool parallelism + retry/backoff** — small, local, immediately visible.
3. **Tool approval tiers** (add an `ask` level between allow and deny) — the local `PolicyEngine` is a clean insertion point; its current hard allow/deny is exactly the shape Continue and Kilo generalised into `allow`/`ask`/`deny`.
4. **AGENTS.md + skills** — trivial reads; the standards are settled.
5. **Subagents** — the architecture supports it; add budgets and caps *at the same time*, not later.
6. **Checkpoint/undo** — go with per-prompt snapshots (Claude Code style) rather than a shadow repo, since the workspace is already container-scoped.
7. **Egress control** — the highest-effort, highest-value security item; note the local harness already has the *model-side* boundary (`model_projection`, cloud gating), so this closes the *tool-side* hole.
8. **MCP** — last, and only if "general harness" is the goal. If the goal is a finance-specialist harness, MCP's value is mostly third-party integrations, and the local YAML tool registry already covers that.

---

## 6. Open-source Python reference architectures

The 2026 state of the art, structurally compared:

| Project | License | Structural pattern it demonstrates | Maintained? |
|---|---|---|---|
| **OpenHands Software Agent SDK** | MIT | **Action→Observation tool framework** (Pydantic `Action`/`Observation` schemas, `ToolBase` → `ToolDefinition` → `ToolRegistry`, MCP-spec `ToolAnnotations`), **event-sourced conversation**, **pluggable SecurityAnalyzer → risk levels → confirmation policy**, **Condenser** interface for context, **LiteLLM** provider abstraction, local↔remote **workspace/Agent Server** portability | ✅ v1.35.0 |
| **SWE-agent** | MIT | **Agent-Computer Interface (ACI)** as the core idea — design the *interface* between model and computer rather than the prompt; single YAML config governs behaviour; built for research/hackability | ◐ **superseded by mini-SWE-agent** per its own README |
| **mini-SWE-agent** | MIT | **Radical minimalism** — the assertion that a ~100-line agent matches SWE-agent's performance. v2. Powering Ramp SWE-Bench and used by Meta, NVIDIA, IBM, Nebius. The "how little is enough" baseline | ✅ |
| **Aider** | Apache-2.0 | **tree-sitter + personalized PageRank repo map** with an exact token budget and per-turn re-injection; **git-as-checkpoint**; architect/editor two-model split | ⚠️ dormant |
| **pydantic-ai-harness** | MIT | **Capability composition** — 50+ self-contained capabilities dropped into `capabilities=[...]`; `Coder` and `Researcher` are themselves `CombinedCapability`s that "come apart the way they went together". Closest to a batteries-included Python harness | ✅ |
| **Moatless Tools** | (per repo) | "Build good tools to insert the right context rather than relying on the agent to reason its way there"; SWE-bench 70.8% @ $0.63/instance w/ Claude 4 Sonnet; `moatless-tree-search` implements SWE-Search (MCTS) | ✅ |
| **SWE-ReX** | MIT | **Execution backend abstraction** — run agent-issued commands in local/Docker/remote/modal environments behind one interface | ✅ |
| **SWE-smith** | — | Synthetic task generation for training/eval scale | ✅ |
| **smolagents** (HuggingFace) | Apache-2.0 | Code-writing agents (the model emits Python rather than JSON tool calls) | ✅ |
| **Open Interpreter** | — | Local code execution as the agent primitive | ✅ |
| **Agent Zero** | — | General-purpose autonomous agent framework | ✅ |
| **Qwen-Agent** | Apache-2.0 | Tool/function-calling framework from Alibaba | ✅ |
| Others collected | — | `opencode`, `deepagents`, `langgraph`, `google-adk-python`, `openai-agents-python`, `crewAI`, `autogen`, `camel`, `devika`, `ag2`, `open_deep_research` | ✅ |

### 6.1 OpenHands SDK — the reference to study first

Its architecture maps onto every dimension in this matrix, which is exactly why it's the best template:

```
openhands/sdk/
├── tool/        Action → Observation contract, ToolBase, ToolRegistry,
│                ToolAnnotations (readOnly/destructive/idempotent/openWorld — MCP spec hints)
├── security/    SecurityAnalyzerBase → LLMSecurityAnalyzer | NoOpSecurityAnalyzer
│                risk levels LOW/MEDIUM/HIGH/UNKNOWN → ConfirmationPolicy.should_require_confirmation()
├── context/condenser/  CondenserBase → NoOp | LLMSummarizing | Pipeline
├── llm/         LiteLLM-backed provider abstraction; completion() + responses();
│                token/cost/latency telemetry
├── conversation/ Event-sourced history; View management
└── workspace/   Local ↔ Agent Server (Docker / Apptainer / process / remote / cloud)
```

Three transferable design decisions:
1. **Tools are typed contracts, not functions.** Pydantic `Action`/`Observation` give you automatic JSON-schema generation for the model, validation before execution, and a uniform error event (`AgentErrorEvent`) — the structural alternative to "patch reliability with prompt text".
2. **Security is a pluggable analyzer, not an if-statement.** Risk is assessed, then a *separate* confirmation policy decides. That separation is what lets a harness add an advisory or classifier later without rewriting dispatch.
3. **Context compression is an interface with multiple implementations.** Swapping `LLMSummarizing` for a `Pipeline` is a config change, not a refactor.

### 6.2 pydantic-ai-harness — the fastest path for a Python harness

If the goal is a capable Python harness quickly rather than learning from first principles, this package already ships most of §4:

- **Execution:** `FileSystem` (path-traversal and symlink safe, secrets read-only), `Shell` (allowlists, denylists, timeouts, **credential-stripping** via `denied_env_patterns=LLM_API_KEY_ENV_PATTERNS`), `ModalSandbox`.
- **Reasoning/delegation:** `Planning`, `SubAgents`, `DynamicWorkflow` (model orchestrates subagents from one Python script, with **hard `max_agent_calls` budgets**), `Advisor` (executor consults a stronger model mid-run).
- **Context:** `Compaction` (tool-result clearing, sliding-window trimming, LLM summarisation, tiered — all window-relative with live usage reporting), `ToolOutputLimits`, `Code Mode` (many tools in one Monty-sandboxed script), `ToolSearch`, `WarnOnCacheBusts`.
- **Knowledge:** `Memory` (namespaced notebook, in-memory/file/Postgres), `ConversationSearch` (BM25 over history *including turns compaction dropped*), `Skills`, `RepoContext` (**loads `AGENTS.md`/`CLAUDE.md` + repo structure**).
- **Control:** `Guardrails`, `PromptInjectionDefender`, `SpendLimits` (cross-window USD/token budgets), tool approval, `SystemReminders` (cache-safe guidance re-injection), `TrajectoryJudge` (a second model reviews the live run every N requests and can steer it).
- **Self-extension:** `CapabilityCreation` (the agent writes, validates and persists new capabilities).
- **Runtime:** durable execution on Temporal/DBOS/Prefect, `StepPersistence` (save/restore/**resume `continue_run`**/**fork `fork_run`**), OTel GenAI spans.

Its **one notable gap** versus the desktop harnesses is **OS-level sandboxing** — isolation is `ModalSandbox` (cloud) rather than Seatbelt/Landlock, so the "sandbox as default local execution mode" tier is missing.

---

## 7. Unverified / could not confirm

Explicitly flagged. **Do not treat these as findings.**

**Codex:** whether compiler/linter/LSP diagnostics are automatically injected into model context; MCP **`prompts`** support (tools and resources confirmed); any official harness-level SWE-bench score; `codex cloud` JSON/CI surface; maturity of the Python SDK (release workflows exist in-repo but the docs were not read); exact CLI availability of `external-sandbox`.

**Cline — RESOLVED since the first draft** (a terminated-then-recovered research stream reported late and upgraded this from the weakest area to fully covered). Now **confirmed**: MCP **resources** and **prompts** (CHANGELOG-only), MCP **OAuth** for both dynamic and pre-registered clients (`mcpOAuthSecrets` in SecretStorage), **no MCP server mode**, `AGENTS.md` native, `SKILL.md` skills, hooks, plugins, shadow-git checkpoints with 3 restore modes, `--worktree`, resume + fork, OTel **metrics+logs but no tracing**, `@problems` (errors-only).
**Remaining Cline gaps / source-class cautions:** **`CLINE.md`, `CLAUDE.md`, `GEMINI.md` not documented** (only `.claude/skills/`); **OS sandbox in 4.x unresolved** (undocumented `CLINE_SANDBOX` vars vs. a third-party audit that found none); devcontainer execution not documented; no first-party GitHub Action repo; **no Cline-published SWE-bench Verified score** — third-party 2026 comparisons quoting numbers are secondary and model-dependent and must be labelled as such; "Cline Bench"/"Context-Bench" appear **only in third-party blogs** — do not print.
**Two source classes that must stay labelled:** the "no OS sandbox" and terminal-model claims come from `agent-safehouse.dev` — an **independent third-party audit**, not Cline docs; and `cline.bot/blog/*` is **first-party blog, not reference documentation**. CHANGELOG evidence is mixed across **3.8x–4.1.x**, so those claims need date qualification.

**Roo Code (historical):** exact final mode list and when/if Orchestrator + Boomerang were removed (vs. just dropped from the README); `.roorules` / `.roo/rules*/`; `.roomodes`; Qdrant indexing; MCP OAuth/governance; SWE-bench numbers; headless/CLI/cloud. `docs.roocode.com/llms.txt` returns 404, so live docs could not be checked. Precise shutdown mechanics beyond the README statement are secondary reporting.

**ZooCode:** benchmark numbers, licensing, telemetry, sandboxing, `SKILL.md`/hooks support. Its README also names models I could not corroborate ("GPT-6 Astra", "Claude Fable 5.1") — vendor claims.

**Kilo:** whether it is officially Roo's successor (**not supported**); whether the current repo is a fork or a rewrite; MCP OAuth; MCP server mode; headless JSON specifics; hook list; checkpointing; LSP diagnostics; benchmark results; telemetry. Kilo's per-topic doc URLs (`kilo.ai/docs/...`) returned 404 and its `api/raw-markdown` endpoint timed out, so all Kilo detail comes from the single large `llms.txt` corpus it publishes.

**Cursor:** codebase-embedding/semantic-index internals (current docs foreground Instant Grep); agent-side diagnostics/LSP feedback; checkpoint retention limits; a single canonical "current version" statement (docs reference 3.0 through 3.11, changelog through 2026-09-02); the reported xAI/SpaceX acquisition (**press only**).

**Aider:** `/cost` does not exist (it is `/tokens`); `--analytics` default is `"random"`.

**Continue:** no documented checkpoints, no git-worktree isolation, no subagent tool, no MCP OAuth, no MCP server mode, no MCP resources/prompts, no OTel, no published evals; `@Codebase`/Context Providers are **deprecated**; the LSP-based-context-provider premise is **not supported** by current official docs; whether `/init`'s `AGENTS.md` is also *read* is undocumented; `summarize` model role is documented as unused. Subagent PR #9128 merge status unverified (GitHub API rate-limited).

**Amp:** tool-level and MCP permission semantics were not read in full; Orbs internals; whether Amp exposes an MCP server mode.

**Devin / Copilot coding agent / Copilot CLI / Jules / Windsurf / Factory / Qodo / Warp:** a delegated research stream covering these was **stopped before reporting**, so **these remain gaps in the matrix, not evidence of weak capability.** Devin itself was subsequently researched directly and is now covered in full in §3.11. Still unresearched: **GitHub Copilot coding agent and Copilot CLI, Google Jules, Factory Droid, Qodo, Warp**. Partially fetched artifacts are preserved under `harness-matrix-2026/raw/commercial/` and `raw-agent/`.

**Devin residual gaps:** exact sandbox implementation per OS (macOS mechanism not stated in the docs read); whether the CLI exposes MCP **server** mode as well as the platform Devin MCP server; hook *handler* types (the event list and matchers were read, but not whether hooks can rewrite arguments as Cursor's `updatedInput` does); DeepWiki internals; ACU-to-dollar conversion semantics; whether `--sandbox` is on by default or opt-in.

**Benchmarks:** `Terminal-Bench 4.0` leaderboard rows are client-rendered, so **no numeric scores could be extracted** — only the metric set (resolution rate, cost, tokens) and hosting (Stanford / Harbor / Laude Institute). Claims that **Harbor** is the Terminal-Bench runner and that **Inspect AI** (UK AISI) is a viable eval framework were **not verified** in this pass.

**Process caveat:** an unrelated process in this workspace repeatedly deleted research directories during the session (the `_research/` tree was wiped at least four times, and the first consolidation target `harness-matrix/` was emptied). All figures above were re-derived from the artifacts mirrored at `C:\Users\Administrator\harness-matrix-2026\raw\` (3,648 files). If that path is gone, treat the inline quotes in this document as the durable record.

---

## 8. Sources

**Standards & protocols**
[AGENTS.md](https://agents.md/) · [AAIF](https://aaif.io/) · [Agent Skills spec](https://agentskills.io/specification) · [Agent Skills clients](https://agentskills.io/clients) · [MCP versioning](https://modelcontextprotocol.io/specification/versioning) · [MCP 2026-07-28 changelog](https://modelcontextprotocol.io/specification/2026-07-28/changelog) · [MCP 2025-11-25 changelog](https://modelcontextprotocol.io/specification/2025-11-25/changelog) · [MCP 2025-06-18 changelog](https://modelcontextprotocol.io/specification/2025-06-18/changelog) · [MCP extensions](https://modelcontextprotocol.io/extensions/overview) · [MCP authorization](https://modelcontextprotocol.io/specification/2025-11-25/basic/authorization) · [MCP registry](https://registry.modelcontextprotocol.io/docs)

**Claude Code**
[Glossary](https://code.claude.com/docs/en/glossary) · [Tools reference](https://code.claude.com/docs/en/tools-reference) · [Sandboxing](https://code.claude.com/docs/en/sandboxing) · [Permission modes](https://code.claude.com/docs/en/permission-modes) · [Permissions](https://code.claude.com/docs/en/permissions) · [Subagents](https://code.claude.com/docs/en/sub-agents) · [Agent teams](https://code.claude.com/docs/en/agent-teams) · [Dynamic workflows](https://code.claude.com/docs/en/workflows) · [Checkpointing](https://code.claude.com/docs/en/checkpointing) · [Hooks](https://code.claude.com/docs/en/hooks) · [Skills](https://code.claude.com/docs/en/skills) · [Plugins](https://code.claude.com/docs/en/plugins) · [Memory](https://code.claude.com/docs/en/memory) · [Headless / Agent SDK](https://code.claude.com/docs/en/headless) · [Monitoring (OTel)](https://code.claude.com/docs/en/monitoring-usage) · [Costs](https://code.claude.com/docs/en/costs) · [Worktrees](https://code.claude.com/docs/en/worktrees)

**Codex**
[Repo](https://github.com/openai/codex) · [config.md](https://raw.githubusercontent.com/openai/codex/main/docs/config.md) · [sandbox.md](https://raw.githubusercontent.com/openai/codex/main/docs/sandbox.md) · [linux-sandbox](https://raw.githubusercontent.com/openai/codex/main/codex-rs/linux-sandbox/README.md) · [network-proxy](https://raw.githubusercontent.com/openai/codex/main/codex-rs/network-proxy/README.md) · [otel](https://raw.githubusercontent.com/openai/codex/main/codex-rs/otel/README.md) · [memories](https://raw.githubusercontent.com/openai/codex/main/codex-rs/memories/README.md) · [execpolicy](https://raw.githubusercontent.com/openai/codex/main/codex-rs/execpolicy/README.md) · [sandboxing concepts](https://mintlify.wiki/openai/codex/concepts/sandboxing)

**Cline** — [docs index](https://docs.cline.bot/llms.txt) · [Subagents](https://docs.cline.bot/features/subagents.md) · [MCP](https://docs.cline.bot/mcp/mcp-overview.md) · [Checkpoints](https://docs.cline.bot/core-workflows/checkpoints.md) · [Plan & Act](https://docs.cline.bot/core-workflows/plan-and-act.md) · [Auto Approve](https://docs.cline.bot/features/auto-approve.md) · [CLI](https://docs.cline.bot/usage/cli-overview.md) · [OpenTelemetry](https://docs.cline.bot/enterprise-solutions/monitoring/opentelemetry.md) · [ClineCore SDK](https://docs.cline.bot/sdk/clinecore.md) · [ACP](https://docs.cline.bot/usage/acp.md)

**Kilo** — [docs corpus](https://kilo.ai/docs/llms.txt) · [Custom Subagents](https://kilo.ai/docs/customize/custom-subagents) · [Agent Permissions](https://kilo.ai/docs/customize/agent-permissions) · [Context Condensing](https://kilo.ai/docs/customize/context/context-condensing) · [Codebase Indexing](https://kilo.ai/docs/customize/context/codebase-indexing)

**Roo Code** — [README (shutdown notice)](https://raw.githubusercontent.com/RooCodeInc/Roo-Code/main/README.md) · [ZooCode](https://github.com/Zoo-Code-Org/Zoo-Code) · [Kilo migration guide](https://kilo.ai/articles/roo-to-kilo-migration-guide)

**Aider** — [options](https://aider.chat/docs/config/options.html) · [repo map](https://aider.chat/docs/repomap.html) · [lint/test](https://aider.chat/docs/usage/lint-test.html) · [git](https://aider.chat/docs/git.html) · [scripting](https://aider.chat/docs/scripting.html) · [leaderboard](https://aider.chat/docs/leaderboards/) · [repomap.py](https://raw.githubusercontent.com/Aider-AI/aider/main/aider/repomap.py)

**Continue** — [docs](https://docs.continue.dev/) · [CLI headless](https://docs.continue.dev/cli/headless-mode) · [tool permissions](https://docs.continue.dev/cli/tool-permissions) · [MCP](https://docs.continue.dev/customize/deep-dives/mcp) · [plan mode](https://docs.continue.dev/guides/plan-mode-guide) · [deprecated @Codebase](https://docs.continue.dev/reference/deprecated-codebase)

**Cursor** — [Hooks](https://cursor.com/docs/hooks.md) · [Subagents](https://cursor.com/docs/subagents.md) · [MCP](https://cursor.com/docs/mcp.md) · [Run Modes](https://cursor.com/docs/agent/security/run-modes.md) · [Rules/AGENTS.md](https://cursor.com/docs/rules.md) · [Worktrees](https://cursor.com/docs/configuration/worktrees.md) · [OTel export](https://cursor.com/docs/enterprise/opentelemetry-export.md) · [Evals](https://cursor.com/evals) · [Changelog](https://cursor.com/changelog)

**Goose** — [README](https://github.com/aaif-goose/goose) · [Adversary mode](https://goose-docs.ai/docs/guides/security/adversary-mode/) · [Extension allowlist](https://goose-docs.ai/docs/guides/allowlist/) · [Multi-model](https://goose-docs.ai/docs/guides/multi-model/) · [Codebase analysis](https://goose-docs.ai/docs/guides/codebase-analysis/) · [Security](https://goose-docs.ai/docs/guides/security/)

**OpenHands** — [SDK](https://github.com/OpenHands/software-agent-sdk) · [docs index](https://docs.openhands.dev/llms.txt) · [Tool system](https://docs.openhands.dev/sdk/arch/tool-system) · [Security](https://docs.openhands.dev/sdk/arch/security) · [Condenser](https://docs.openhands.dev/sdk/arch/condenser) · [Hooks](https://docs.openhands.dev/sdk/guides/hooks) · [Observability](https://docs.openhands.dev/sdk/guides/observability) · [Sandboxes](https://docs.openhands.dev/openhands/usage/sandboxes/overview) · [Tech report](https://arxiv.org/abs/2511.03690)

**Amp** — [docs](https://ampcode.com/docs) · [Modes & models](https://ampcode.com/docs/models-and-subagents) · [MCP registry allowlist](https://ampcode.com/docs/enterprise/mcp-registry-allowlist) · [Execute mode](https://ampcode.com/docs/cli/execute-mode) · [AGENTS.md](https://ampcode.com/docs/customize/agents-md)

**Devin / Cognition** — [CLI docs index](https://docs.devin.ai/llms.txt) · [CLI sandbox](https://docs.devin.ai/cli/sandbox.md) · [CLI subagents](https://docs.devin.ai/cli/subagents.md) · [Lifecycle hooks](https://docs.devin.ai/cli/extensibility/hooks/lifecycle-hooks.md) · [Adaptive router](https://docs.devin.ai/cli/adaptive.md) · [Advanced capabilities / managed Devins](https://docs.devin.ai/work-with-devin/advanced-capabilities.md) · [Devin MCP server](https://docs.devin.ai/work-with-devin/devin-mcp.md) · [Security profiles](https://docs.devin.ai/product-guides/security-profiles.md) · [DeepWiki](https://docs.devin.ai/work-with-devin/deepwiki.md) · [Outposts](https://docs.devin.ai/cloud/outposts/overview.md) · [FrontierCode](https://cognition.com/frontiercode)

**Benchmarks & Python references** — [SWE-bench leaderboard](https://www.swebench.com/) · [SWE-bench](https://github.com/SWE-bench/SWE-bench) · [SWE-bench Pro](https://scale.com/blog/swe-bench-pro) · [Terminal-Bench](https://www.tbench.ai) · [mini-SWE-agent](https://github.com/SWE-agent/mini-swe-agent) · [SWE-agent](https://github.com/SWE-agent/SWE-agent) · [SWE-ReX](https://github.com/SWE-agent/SWE-ReX) · [SWE-smith](https://github.com/SWE-bench/SWE-smith) · [sb-cli](https://github.com/SWE-bench/sb-cli) · [Moatless Tools](https://github.com/aorwall/moatless-tools) · [pydantic-ai-harness](https://github.com/pydantic/pydantic-ai-harness) · [Inside the Scaffold (arXiv 2604.03515)](https://arxiv.org/abs/2604.03515)
