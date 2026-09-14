# AGENTS.md — 本仓库协作规则

> 给在本仓库工作的 AI 协作者与人协作者。**先读本文再动手。**

## 铁律：唯一入口、唯一数据

1. **用户准入入口只有一个**：根目录 `启动.bat` → `scripts/start-daily.ps1`（BFF @ 127.0.0.1:9510，同源托管 Web 界面）。任何新功能、测试、人工验收都必须能通过这同一个入口使用；**禁止**新增或复活第二条启动路径（旧的 quick-start / start-in1 / run_in1 / compose.in1 / TUI / Ink CLI 已全部移除）。
2. **数据只有一份**：会话库 `data/conversations.db`、工作区 `data/workspace/`、模型账本 `data/model-calls.sqlite`。隔离/验收数据通过**独立会话 + 独立股票状态目录**实现，不要再造独立库。历史 IN1 数据已并入日常库（`data/_archive/in1/` 留有原始备份）。
3. **退出也只有一条路径**：页面"退出"按钮或关闭页面（约 15 秒自动全退，含 docker 容器，见 `core/auto_exit.py`、`bff/app.py` 的 `_request_stack_shutdown`）。不要再加"只停某一层"的用户可见按钮。
4. 页面上的服务管理只以**技能开关**形式存在（`core/skills_service.py` 联网搜索；`core/stock_service.py` 股票后台=股票技能开关）。新增可开关能力时沿用这个模式：持久化开关 + 进程/容器生命周期 + 工具注册表动态翻转（`apply_to`）。

## 常用命令

```powershell
.\scripts\start-daily.ps1        # 启动（= 双击 启动.bat）；重复运行只开浏览器
.\scripts\check-env.ps1          # 环境自检
.\scripts\stop-backend.ps1       # 应急：只停聊天后端（页面打不开时用）
.\.conda\python.exe -m unittest discover -s tests   # 离线测试（当前 117 项）
npm run build --prefix apps/web  # 前端构建（BFF 托管 apps/web/dist）
docker compose up -d             # 工具容器（websearch profile 由页面开关管理）
```

## 结构速览

- `bff/app.py`：HTTP 层（聊天、会话、工作区、模型、技能开关、股票服务管理、心跳、退出）。退出语义：**先股票优雅收尾 → 停 docker 容器 → 杀本进程**。
- `core/runtime.py`：`build_runtime()` 组装工具注册表/路由/模型目录；技能开关通过替换 `tool_registry._tools` 与 `router._backend_urls` 动态生效（注意保持 registry 对象同一性，见 `stock_service.attach` 与 `skills_service.apply_to` 的注释）。
- `bff/service.py`：`ChatSessionService` 是会话/工作区/流式门面；`exclusive_turn` 保证单会话互斥，技能切换期间聊天 409。
- 前端 `apps/web/src/App.tsx` 是单页全部主界面；右栏自上而下：工作区、外观、模型/Provider/工具、状态（唯一"退出"按钮）、技能面板、股票技能面板。
- 提示词在 `config/prompts/`（从旧 gateway/ 迁入；`core/config.py` 的 `PROMPTS_DIR` 指到这里）。

## 约定

- 两个仓库直接工作在 `main`；提交小而可审阅，按批次（INx/N2 等）记录到股票仓库 `docs/计划.md`。
- 数据与密钥不落库提交：`.env`、`data/` 均 gitignore；密钥只经页面"模型设置"保存到 `data/private/model-keys.json`。
- 验收回归方法见 `docs/回归验证指南.md`；日常口径见 `docs/日常运行指南.md`；部署见 `本地AI-Agents部署流程.md`。改启动/退出/数据结构时必须同步这些文档与本文件。
- 涉及股票业务的改动先看 `docs/股票能力接入.md` 的模块归属：聊天侧只做模型路由、工具面与卡片展示；业务规则在股票仓库。
