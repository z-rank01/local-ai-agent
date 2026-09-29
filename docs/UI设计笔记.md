# UI 设计笔记（前端施工规范）

> 日期：2026-09-28。状态：**现行规范**（随 U1–U5 落地，U6 验收后默认约束全部 `apps/web/**` 改动）。
> 优先级：根目录 [AGENTS.md](../AGENTS.md) 铁律优先；批次进度以 [前端优化计划](./前端优化计划.md) 为准。
> 适用：任何新增功能、新面板、新模块在 `apps/web/` 内的 UI 施工。对照本文做，不再自由发挥。

## 1. 生效方式（改了不生效先查这里）

| 事项 | 做法 |
| --- | --- |
| 构建 | `npm run build --prefix apps/web`（tsc + vite），BFF 托管 `apps/web/dist`，构建后刷新页面才生效 |
| esbuild 临时目录报错 | Windows 上若报 `remove ... esbuild-* Access is denied`，用项目内临时目录重跑：`TEMP="…\data\tmp\esbuild-tmp" TMP="…\data\tmp\esbuild-tmp" npm run build --prefix apps/web` |
| 单测 | `.\.conda\python.exe -m unittest discover -s tests`（改动后保持全绿） |
| 行尾 | `styles.css` 全文件纯 CRLF；tsx/ts 为 LF；提交前不要整文件翻转行尾 |

## 2. 主题令牌（唯一颜色/尺寸来源）

`styles.css :root`（1-62 行）是唯一定义处。**新增样式只允许引用令牌，禁止新硬编码颜色、间距、圆角、字号、阴影、时长。**

| 组 | 令牌 | 用途 / 禁则 |
| --- | --- | --- |
| 表面 | `--bg` `--surface` `--surface-soft` `--surface-inset` | 底/卡片/次级/陷层（代码块、预览、时间线详情）。禁再写 `#fffefa` 之类近似白 |
| 线条 | `--line` `--line-strong` | 细线/强调线，边框只允许这两个（或语义色） |
| 文字 | `--text` `--text-strong` `--muted` `--muted-soft` `--text-on-accent` | `--muted-soft` 仅装饰（对比度约 3:1，禁用于正文） |
| 强调 | `--accent` `--accent-soft` `--accent-strong` | 交互/选中/链接唯一强调色（紫系）。禁引入第二种强调色 |
| 语义 | `--ok` `--warn` `--danger` + `-soft` 底 | 状态 pill 必须语义色差：完成=ok、进行/等待=warn、失败=danger |
| 层级 | `--shadow` `--shadow-soft` `--elev-1/2/3` `--ring-focus` | 浮层按层级选档；焦点环全应用统一 `--ring-focus` |
| 阶梯 | `--space-1..6`(4/6/8/12/16/24) `--radius-sm/md/lg/xl`(8/10/12/16) `--text-xs..xl`(11/12/13/15/17/20) | 新规则必须用阶梯；胶囊用 `999px` |
| 动效 | `--duration-fast/normal/slow`(160/240/360ms) `--ease-standard` `--ease-emphasized` | 禁直写时长与曲线 |
| 布局 | `--sidebar-width` `--inspector-width` | 由 App.tsx `shellStyle` 注入（用户拖宽并持久化）；`:root` 值是回退默认 |

用户外观偏好（字体/字号/行高/阅读宽度/密度）经 `shellStyle` 内联注入同名变量；**改默认值要同时改 `:root` 与 `DEFAULT_APPEARANCE`**（两处，已在代码注释互相标注）。

## 3. 布局骨架

| 区域 | 内容 | 规则 |
| --- | --- | --- |
| 左栏 `.sidebar` | 新建会话、搜索、会话列表；底部固定区 `.sidebar-footer`：PackageTasks + 状态点 + 设置（右栏开关）+ **退出（全应用唯一入口）** | 退出不得再放到别处；侧栏收起时经"会话"恢复条可还原 |
| 中央 `.chat-panel` | 顶栏（会话名、ModelPicker、模型设置、导出、面板开关）+ 会话区 + 输入区 | 顶栏不放状态伪装按钮、不放工作区路径 |
| 右栏 `.inspector` | `.inspector-panels`：模型 → 技能 → 股票技能 → 工作区 → 外观(默认折叠) → 诊断(默认折叠) → 状态；或 `.report-reader` 阅读模式 | 两容器 `display:none` 互切保滚动位置；新面板按频率插入顺序 |
| 断点 | ≤1180px 右栏变抽屉（JS 常量 `INSPECTOR_DRAWER_BREAKPOINT` 与 CSS 媒体查询一一对应，改要同步）；≤820px 单栏；≤700px 顶栏压缩 | 新布局必须过三档宽度：1920/1366/1024 |

## 4. 组件模式

| 模式 | 做法 | 证据/位置 |
| --- | --- | --- |
| 小节骨架 | 统一 `components/Panel.tsx`（标题+操作区+折叠，折叠状态存 localStorage）；面板组件（Workspace/Skills/StockService）自带 section 不套 Panel | `components/Panel.tsx` |
| 按钮分级 | primary=主行动、secondary=次行动、ghost=弱操作、danger=破坏操作；`tiny` 为小尺寸 | 确认分级：删文件/删会话/关股票后台/退出**必须确认**，其余不确认 |
| 状态语言 | `statusLabels.ts` 是唯一中文映射来源；pill 用 `status-pill status-{status}` 类 + `statusLabel()` 文本；**禁止直出英文枚举**，新状态先加映射 | `apps/web/src/statusLabels.ts` |
| 三态 | 加载=`loading-card`/status-hint、空态=`status-hint` 一句话给下一步、错误见下行 | 占位文案不假设布局方向（不写"右侧"） |
| 错误通道 | 会话级错误=`.error-banner`（`role="alert"`、可关闭）；面板操作错误=toast（`role="status"`、2.2s）；面板内字段级=`role="alert"` 就地 | 同一错误只走一个通道 |
| 执行详情 | 多工具=时间线卡（步骤可展开看完整参数/结果/错误/耗时）；单工具=同一折叠卡语义；不得双份渲染 | `AssistantTranscriptItem` |
| 报告 | `local_result` 渲染为 ReportCard（标题/交易日/页码/版本/生成时间 + "阅读全文"），右栏阅读模式可放大/关闭；**措辞中性，只说"本地报告/已生成"，不暗示模型已读或未读** | `ReportCard`、`readingReport` |
| 会话/文件行 | 行主体是 `button`（`-open` 类），操作钮是行内并列真 button；**禁止 role=button 内嵌 button、禁止 span onClick 伪按钮** | `ConversationItem`、`WorkspacePanel` |

## 5. 动效预算

| 场景 | 时长 | 说明 |
| --- | --- | --- |
| 菜单/tooltip/小浮层出现 | fast(160ms) | opacity + 2px 位移内 |
| 面板折叠、toast、阅读模式切换 | normal(240ms) | 克制，不做高度弹簧 |
| 布局栏宽变化 | slow(360ms) | 仅布局级 |
| 长任务 | 小型状态点（typing-dot） | 禁止百分比编造；`infinite` 动画全应用只此一个 |
| reduced-motion | 文件末尾全局规则（1ms + 单次） | 新增动画**不需要**再登记清单，但必须被全局规则覆盖有效 |

新 keyframes 命名 `motion-*`；挂载动画注意列表 key 稳定性，避免重放。

## 6. 可访问性 checklist（新交互必过）

- 键盘可达：所有操作可 Tab 到达并触发；焦点环可见（`--ring-focus`，禁 `outline:none` 裸删——用 `:focus:not(:focus-visible)` 模式）
- 可访问名：图标按钮必须有 aria-label 或可读文本；伪按钮（span onClick）零容忍
- 弹层：关闭后焦点回触发按钮；Escape 可关
- 触屏：不得只靠 `:hover` 暴露操作（用 `@media (hover: none)` 或低透明度常显）
- 对比度：正文 ≥4.5:1、非文本 ≥3:1；状态不只用颜色传达（配文字/图标）
- 输入法：文本输入的 Enter 快捷键先判 `event.nativeEvent.isComposing`

## 7. 施工规则（违反=返工）

1. **只用令牌**：新样式引用第 2 节令牌；缺令牌时在 `:root` 补定义再用，不在使用处写字面量。
2. **禁止追加覆盖**：改样式改原规则，不在文件后追加同名选择器覆写（本仓库曾为覆盖堆付出 5 个反向覆盖 bug 的代价）。新增组件样式放文件尾部对应批次区块。
3. **状态/枚举必须中文**：来自后端的枚举经映射层（`statusLabels.ts` 或面板内 labels）转中文再渲染；内部 ID（decision_id、端口、operation_id 等）不进界面文案。
4. **文案**：省略号全角"…"；不写布局方向假设；确认分级见第 4 节按钮行。
5. **不加依赖**：新需求先用现有栈（react-markdown/highlight.js 等）解决；确需新库先征得同意。
6. **铁律相关**：新可开关能力沿用"技能开关"模式；不加第二启动/退出路径；页面不暗示可直接操控 docker/股票进程（启动器 9511 托管）。
7. 提交前：build + 单测过；`git status` 只提交本任务文件。

## 8. 新功能 UI checklist（按序过一遍）

| 步 | 问题 | 依据 |
| --- | --- | --- |
| 1 | 它属于哪个区域？（左栏/中央/右栏/弹层/阅读模式） | 第 3 节 |
| 2 | 用户需要什么状态？（加载/空/错误/成功/进行中） | 第 4 节三态行 |
| 3 | 状态从哪来？后端枚举的中文映射加了吗？ | `statusLabels.ts` |
| 4 | 复用哪个模式？（Panel/按钮分级/详情折叠/报告卡） | 第 4 节 |
| 5 | 样式全用令牌了吗？追加覆盖了吗？ | 第 7.1/7.2 |
| 6 | 键盘/触屏/读屏可用吗？ | 第 6 节 |
| 7 | 需要动效吗？在预算内吗？ | 第 5 节 |
| 8 | build + 单测过了吗？需要截图验收吗？ | 第 1 节 |

## 9. 待后端配合清单（前端不越界，需要时先开对应批次）

| 项目 | 需要的改动 | 归属 |
| --- | --- | --- |
| 工具面板裸列内部标识符 | 工具清单 API 增加分组/中文显示名 | 本仓库 `config/tools/*.yaml` + bff |
| 股票枚举裸展示（B04/B8 编号等） | observation 直接给中文标签 | 股票仓库 |
| "人工操作"命令行式交互 | 动作化接口（按钮+结构化结果） | 股票仓库 |
| 搜索无命中证据 | `/api/conversations?query=` 返回 snippet | 本仓库 bff + `core/conversation_store.py` |
| 契约字段漂移 | `bff/schemas.py` 与 `types.ts` 对齐或裁剪 | 本仓库 bff |
| Panel 高度展开动画 | Panel.tsx body 常驻 + is-collapsed 类（约 3 行）后补 | 本仓库前端（U5 遗留） |

## 相关文档

- 批次基准与进度：[前端优化计划](./前端优化计划.md)
- 协作规则：[../AGENTS.md](../AGENTS.md)、[文档记录简则](./文档记录简则.md)
- 验收方法：[回归验证指南](./回归验证指南.md)
