# UI 设计笔记（前端施工规范）

> 日期：2026-10-07（原型 A「沉浸会话」整体重写后更新）。状态：**现行规范**，约束全部 `apps/web/**` 改动。
> 优先级：根目录 [AGENTS.md](../AGENTS.md) 铁律优先；批次进度只维护股票仓库 `docs/计划.md`。主题方案见[深色模式落地计划](./深色模式落地计划.md)。
> 适用：任何新增功能、新面板、新模块在 `apps/web/` 内的 UI 施工。对照本文做，不再自由发挥。

## 1. 生效方式（改了不生效先查这里）

| 事项 | 做法 |
| --- | --- |
| 构建 | `npm run build --prefix apps/web`（tsc + vite），BFF 托管 `apps/web/dist`，构建后刷新页面才生效 |
| 类型检查 | `npm run typecheck --prefix apps/web`（只跑 tsc，不产出 dist） |
| esbuild 临时目录报错 | Windows 上若报 `remove ... esbuild-* Access is denied`，用项目内临时目录重跑：`TEMP="…\data\tmp\esbuild-tmp" TMP="…\data\tmp\esbuild-tmp" npm run build --prefix apps/web` |
| 唯一入口构建缓存 | `-RebuildWeb`已在构建子进程期间使用`data/private/web-build-tmp/`，结束恢复原环境；失败查看`data/logs/web-build.log`，不改系统临时目录ACL |
| 单测 | `.\.conda\python.exe -m unittest discover -s tests`（改动后保持全绿） |
| 行尾 | `styles.css` 全文件纯 CRLF；tsx/ts 为 LF；提交前不要整文件翻转行尾 |

## 2. 主题令牌（Apple 设计系统，唯一定义处）

`styles.css :root`（四组：基础令牌 → 字体本地化 → color-mix 派生令牌 → 容器嵌套）是唯一颜色/尺寸/动效定义处；令牌与组件样式自定稿原型 `harness-a-immersive.html`（Apple 设计系统）逐条移植。**新增样式只允许引用令牌，禁止新硬编码颜色、间距、圆角、字号、阴影、时长；缺令牌时先在 `:root` 补定义再用。**

| 组 | 令牌 | 用途 / 禁则 |
| --- | --- | --- |
| 表面 | `--bg` `--surface` `--surface-warm` | 底 / 次级面 / 微暖白卡（报告卡、设置底） |
| 文字阶梯 | `--fg` `--fg-2` `--muted` `--meta` | 主文 / 次文 / 弱文 / 元信息（等宽小字）；再弱不得用于正文 |
| 线条 | `--border` `--border-soft` | 边框只允许这两个（或语义色） |
| 强调 | `--accent` `--accent-on` `--accent-hover` `--accent-active` | 交互/选中/链接唯一强调色（Apple 蓝 `#0071e3`）。禁引入第二种强调色 |
| 语义 | `--success` `--warn` `--danger` | 状态必须语义色差：完成=success、进行/等待=warn、失败=danger |
| 派生色 | `--accent-soft` `--accent-soft-2` `--fg-soft` `--fg-soft-2` `--surface-inset` `--surface-track`；`--ok-soft` `--warn-soft` `--danger-soft` `--ok-text` `--warn-text` `--danger-text` | 由基色 color-mix 派生，禁用新的近似色硬编码 |
| 石墨黑（轨迹/阅读） | `--ink` `--ink-2` `--ink-3` `--ink-line` `--ink-line-2` `--on-ink` `--on-ink-2` `--on-ink-3` `--accent-on-ink` `--ok-on-ink` `--warn-on-ink` `--danger-on-ink` | 执行轨迹、代码卡、报告阅读器在两种主题中均保持深色；深色主题显式覆盖石墨黑令牌，不能随页面反白 |
| 主题语义 | `--link` `--knob` `--scrim` `--card` `--inverse-hover` `--code-label` `--selected-text` `--caption-text` | 分别用于链接、旋钮/选中片、遮罩、原型指定卡片、反色按钮悬停、代码元信息、选中文字和小标签；不能将所有背景都改成卡片色 |
| 半径 | `--radius-sm` `--radius-md` `--radius-lg` `--radius-pill`（8/12/18/980） | 胶囊一律 `--radius-pill` |
| 阶梯 | `--text-xs..lg`（12/14/17/21）`--tracking-display`；`--space-2/3/4/6`（8/12/16/24） | 新规则必须用阶梯 |
| 容器嵌套 | `--nest-pad` `--nest-pad-in` `--nest-gap-2` `--nest-head-y` | 一级=卡片、二级=发丝行；不再卡套卡 |
| 阴影/焦点 | `--elev-ring` `--elev-raised` `--shadow-card` `--shadow-pop` `--shadow-float` `--focus-ring` | 浮层按层级选档；焦点环全应用统一 `--focus-ring` |
| 动效 | `--motion-fast`（150ms）`--motion-base`（220ms）`--ease-standard` | 禁直写时长与曲线 |
| 布局 | `--container-gutter-desktop/phone`；`--sheet-w`（Sheet 元素）、`--sheet-inset`（documentElement）；`--ui-font` `--ui-size` `--ui-leading` `--code-font` `--code-size` `--read-width` | 见第 3 节让位机制与外观偏好 |

- **旧紫系令牌已全部废弃**：`--text` `--text-strong` `--line` `--line-strong` `--surface-soft` `--muted-soft` `--accent: #5f5bda` `--sidebar-width` `--inspector-width` `--dur-1/2/3` `--elev-1/2/3` `--ring-focus` `--radius-xl` `--text-xl` 等一律不再引用；看到旧名按本表替换。
- 外观偏好（字体/字号/行高/阅读宽度/密度）由 `App.tsx` 写到 `document.documentElement` 的同名变量消费；默认值唯一来源是 `lib/appearance.ts` 的 `DEFAULT_APPEARANCE`（`:root` 同名值是未注入时的兜底，改默认值两处一并核对）。
- 密度只缩放 `--space-2/3/4/6`（`densityScale`：紧凑 0.86 / 标准 1 / 宽松 1.14），不缩放字号与圆角。

### 三态主题（2026-10-10）

视觉来源是[归档深色原型](./原型/harness-a-dark-mode.html)（桌面同名文件原样复制），布局沿用当前沉浸会话。浅色配色保持原值；深色由 `:root[data-theme='dark']` 覆盖，纯黑舞台和石墨黑层级分别消费自己的令牌。

| 令牌组 | 深色值 |
| --- | --- |
| 舞台 / 表面 / 微暖面 / 卡片 | `#000` / `#1c1c1e` / `#0c0c0e` / `#2c2c2e` |
| 主文 / 次文 / 弱文 / 元信息 | `#f5f5f7` / `#c7c7cc` / `#98989d` / `#98989d` |
| 边框 / 软边框 / 链接 | `#38383a` / `#2c2c2e` / `#2997ff` |
| 完成 / 等待 / 失败 | `#30d158` / `#ffd60a` / `#ff453a` |
| 石墨黑三层 | `#1c1c1e` / `#2c2c2e` / `#3a3a3c` |

元信息、代码标题及模型选项小字在深色中采用原型更亮的文字阶梯，保证其实际背景上的对比度。选中文字统一消费 `--selected-text`，深色将链接蓝与主文字按 80%/20% 混合，保留蓝色选中语义并满足对比度；选中背景与勾选图标继续消费原有令牌。反色按钮悬停背景使用次文亮色，图标继续用舞台色；主题分段控件键盘焦点使用统一焦点环。浅色语义别名维持原计算颜色。

石墨黑模块的文字在正常、hover、focus 和展开状态均使用 `--on-ink` 家族，禁止以页面背景 `--bg` 充当亮文字；深色页面背景是黑色。

`AppearanceSettings.theme` 是 `system | light | dark`，默认及重置为 `system`。沿用 `local-ai-agent.appearance.v1`，旧字段和字体偏好继续保留；缺失或无效主题回退系统。`index.html` 在 CSS/React 前初始化，运行期 `lib/theme.ts` 写根元素 `data-theme`，浮层继承同一主题。仅系统态监听媒体变化，切换或卸载清理监听；存储损坏、拒绝访问及媒体查询不可用均有回退。两处规则由 `npm run test:theme --prefix apps/web` 校验一致性。桌面窗口只改变页面内容。

## 3. 布局骨架（无常驻侧栏）

| 区域 | 内容 | 规则 |
| --- | --- | --- |
| 顶栏 `.topbar` | 会话标题（点击开抽屉）、模型选择器、导出 / 模型设置 / 设置 / 工作台开关 | 固定细条 44px；宽屏对称网格让标题居中于阅读区域，操作组靠右；容器查询按实际可用宽度切换紧凑布局，避免工作台打开时重叠；右边界消费 `--sheet-inset` |
| 阅读栏 `.transcript` / `.thread` | 全部消息、执行轨迹、报告卡 | 全屏滚动容器（`#transcript`），内容列宽 `--read-width` 居中；仅在底部时粘性跟随（80px 阈值），上翻不抢回 |
| 输入胶囊 `.composer-wrap` / `.composer` | 上沿统一运行行、附件 chips、文本域、上传 / 发送 | 当前任务状态及暂停/继续/停止只在上沿运行行；生成中发送禁用，文本域仍可起草。Enter 发送、Shift+Enter 换行、输入法组字中的 Enter 不发送 |
| 会话抽屉 `.drawer`（+ `.scrim` 遮罩） | 新建 / 搜索 / 会话列表（行内导出·重命名·删除）；底部 PackageTasks、服务状态、**退出（全应用唯一入口）** | 左侧覆盖式；退出不得再放到别处 |
| 工作台 `.sheet` | 工作台视图（工作区 / 技能两个 Panel）与文档预览视图共用；左缘 `.sheet-resize` 拖宽 | 右侧覆盖式；宽 340–760px（默认 400），双击复位、方向键步进 16px，宽度存 localStorage；两个滚动容器常挂载、`hidden` 切换保滚动位置 |
| 设置 `.settings` | 全屏模态：外观 / 模型 / 工具与技能 / 诊断 四页签 | Escape 关闭；窄屏页签转顶部横排 |
| 阅读器 `.reader` | 报告沉浸阅读（石墨黑） | 全屏；默认列宽 680px、放大 920px；Escape / 关闭返回 |
| 浮层 | 会话抽屉与工作台 Sheet 两级浮层 | **默认一律收起**，按需唤出；Escape 逐层退出：阅读器 → 设置 → 文档视图 → 工作台 → 抽屉 |

- **`--sheet-inset` 让位机制**：工作台打开时 `App.tsx` 把工作台宽度写到 `document.documentElement` 的 `--sheet-inset`（关闭写 `0px`），`.topbar` / `.transcript` / `.notice-bar` / `.composer-wrap` 消费它做右边界让位；输入胶囊和错误条的宽度也须扣除让位量，剩余内容宽不足 560px 时切为覆盖模式；Sheet 自身宽度用元素内 `--sheet-w`。新增全宽区域要接这套机制，不要再写固定 `right: 0`。
- 断点：≤720px 顶栏压缩、设置页签转横排；≤560px 抽屉与 Sheet 满宽、阅读栏收边；360px 起无横向滚动。新布局必须过 1920 / 1366 / 1024 三档宽度（另核 720 / 560 断点行为）。
- 关闭态过渡：抽屉/Sheet 用 `visibility` + `pointer-events` 延时切换保住 transform 动画，**不要用 `display:none`**。

## 4. 组件模式

| 模式 | 做法 | 证据/位置 |
| --- | --- | --- |
| 折叠基元 | 所有折叠/展开统一走 `ui/Collapse`：`grid-template-rows: 0fr→1fr` + 内层 `.x-in`（overflow hidden、min-height 0），内容 padding 放再下一层；展开态由宿主 `data-open` / `data-collapsed` 的直接子选择器驱动 | `ui/Collapse.tsx`；禁用 max-height / height 过渡与条件卸载 |
| 小节骨架 | Sheet 内小节统一 `ui/Panel`（标题 + meta + 操作区 + 折叠，状态存 `local-ai-agent.panel.{id}`）；**折叠时子组件保持挂载**，面板内轮询/滚动状态不因收起丢失；`forceOpenSignal` 可受控展开 | `ui/Panel.tsx`；Workspace/Skills 由 Sheet 用 Panel 包裹，StockPanel 在技能卡展开体内 |
| 浮层基座 | `ui/Popover` 必须 `createPortal` 到 `document.body`，fixed 贴锚点、横向夹在视口内、空间不足向上翻；滚动/外部点击/Escape 关闭并回焦锚点 | `ui/Popover.tsx`；**禁止给抽屉/Sheet 等祖先加 `will-change: transform`**——会为 fixed 后代创建包含块，令浮层定位整体偏移（`styles.css` 的 `.drawer,.sheet` 注释已标注） |
| 其他基元 | `Dropdown`（Popover + listbox，方向键/Home/End）、`Switch`（role=switch）、`Accordion`（手风琴）、`Icon`（sprite 描边）、`Markdown`（react-markdown + gfm + highlight + katex） | `ui/*` |
| 按钮分级 | `btn-primary`=主行动、`btn-secondary`=次行动、`btn-quiet`=弱操作、`btn-dark`=深色主行动、`btn-danger`=破坏操作；`btn-sm` 为小尺寸 | 确认分级：删文件/删会话/删消息/关闭股票后台/退出**必须确认**，其余不确认 |
| 确认与表单 | 禁止 `window.confirm` / `window.prompt`；用 `lib/confirm` 的 `askConfirm`（危险操作）与 `askForm`（重命名等），返回 Promise，由全局 `ConfirmDialog` 队列渲染 | `lib/confirm.ts`、`ui/ConfirmDialog.tsx` |
| 状态语言 | `statusLabels.ts` 是唯一中文映射来源；状态展示用 `.msg-status`（`data-tone=ok/running/error`）+ `statusLabel()` 文本，徽章用 `.chip-*`、轨迹用 `.trace-state[data-tone]`；面板内枚举（股票 labels/pendingText 等）同样转中文；**禁止直出英文枚举** | `apps/web/src/statusLabels.ts` |
| 三态 | 加载=`.loading` 行 / `dots`；空态=`.empty` / `.ws-empty` 一句话给下一步；错误见下行 | 占位文案不假设会随断点失效的方向 |
| 错误通道 | 会话级错误=`Transcript` 的 `.notice-bar.notice-err`（`role="alert"`、可关闭、离线带"重新连接"）；面板/浮层操作失败=`lib/notify` 的 toast（`role="status"`、2.2s）；面板内状态错误=`.panel-ok` 就地（`role="alert"`） | 同一错误只走一个通道 |
| 执行轨迹 | 石墨黑 `.trace`：整轨折叠 + 每步可展开；步骤双色轴 `think`（蓝）/ `tool`（灰）/ `answer`（绿），状态 tone ok/error/running；单/多工具同一版式，不得双份渲染 | `app/ToolTrace.tsx` |
| 会话运行状态 | 输入胶囊上沿一条紧凑状态行，展示后端运行状态、步骤和连接说明，并集中控制；结束后隐藏，存在安全继续入口时保留。正文末尾、回答占位和输入左下角不再重复运行提示；顶栏总任务数量及轨迹步骤状态用途独立 | `App.tsx`、`Composer.tsx`；断开观察连接不表示后端停止 |
| 执行说明与正文 | 程序展开的证据需求仅保存协议并显示为工具步骤说明，不当作助手回答。旧重复消息仅在与紧邻工具协议全文配对时隐藏，不删除原始历史，不按文字前缀批量清理 | `core/agent.py`、`bff/service.py` |
| 会话报告阅读 | `ReportCard`（本地报告 + 页/版本/时间 chips + "阅读全文"）直接进入 `.reader` 宽屏阅读；首/前/后/末页按钮读取同一报告，失败保留原页，关闭返回会话；正文和折叠区复用Markdown。文件预览仍走Sheet；**措辞中性，不暗示模型已读或未读** | `app/ReportCard.tsx`、`app/Reader.tsx`、`lib/report.ts` |
| 会话/文件行 | 行主体是 `button`，操作钮是行内并列真 button；**禁止 role=button 内嵌 button、禁止 span onClick 伪按钮** | `app/ConversationDrawer.tsx`、`panels/WorkspacePanel.tsx` |

## 5. 动效预算

| 场景 | 时长 | 说明 |
| --- | --- | --- |
| 菜单/浮层/悬停 | fast(150ms) | 背景/透明度/旋转；位移 ≤6px、缩放 ≥0.98 |
| 折叠、抽屉与 Sheet、设置、阅读器、toast | base(220ms) | 克制，不做高度弹簧 |
| 消息/列表入场 | base + `rise`（8px 位移） | 注意 key 稳定性，避免重放 |
| 长任务 | `pulse-ring`(1.4s) / `dots`(1.2s) / `spin`(900ms) 三个持续指示 | 禁止百分比编造；新增持续动画前先看能否复用 |
| reduced-motion | 文件末尾全局规则（1ms + 单次） | 新增动画**不需要**再登记清单，但必须被全局规则覆盖有效 |

新 keyframes 用语义名（`rise` / `fade` / `pop-in` / `dots` / `spin` / `pulse-ring`）；时长与曲线只引用 `--motion-*` / `--ease-standard`。

## 6. 可访问性 checklist（新交互必过）

- 键盘可达：所有操作可 Tab 到达并触发；焦点环可见（`--focus-ring`，`:focus-visible` 统一实现，禁裸删 `outline:none`）
- 可访问名：图标按钮必须有 aria-label 或可读文本；伪按钮（span onClick）零容忍
- 弹层：关闭后焦点回触发按钮；Escape 可关；Popover 打开时焦点移入第一个可聚焦元素
- 触屏：不得只靠 `:hover` 暴露操作（用 `@media (hover: none)` 或低透明度常显）
- 对比度：正文 ≥4.5:1、非文本 ≥3:1；状态不只用颜色传达（配文字/图标）
- 输入法：文本输入的 Enter 快捷键先判 `event.nativeEvent.isComposing`

## 7. 施工规则（违反=返工）

1. **只用令牌**：新样式引用第 2 节令牌；缺令牌时在 `styles.css :root` 补定义再用，不在使用处写字面量。
2. **禁止追加覆盖**：改样式改原规则，不在文件末尾追加同名选择器覆写（本仓库曾为覆盖堆付出反向覆盖 bug 的代价）；新增组件样式写在所属组件的样式区块。
3. **折叠统一走 `Collapse`**：新增折叠/展开不得另行发明（max-height、height 过渡、条件卸载都不许）；需要保状态的折叠内容必须保持挂载。
4. **禁止 `window.confirm` / `window.prompt`**：确认与表单统一走 `lib/confirm` 的 `askConfirm` / `askForm`（全局 ConfirmDialog 队列）。
5. **状态/枚举必须中文**：来自后端的枚举经映射层（`statusLabels.ts` 或面板内 labels）转中文再渲染；内部 ID（decision_id、端口、operation_id 等）不进界面文案。
6. **文案**：省略号全角"…"；用「工作台」「会话列表」等界面名称指路，不写会随断点失效的方向假设；确认分级见第 4 节按钮行。
7. **不加依赖**：新需求先用现有栈（react-markdown / highlight.js / katex 等）解决；确需新库先征得同意。
8. **铁律相关**：新可开关能力沿用"技能开关"模式；不加第二启动/退出路径；页面不暗示可直接操控 docker/股票进程（启动器 9511 托管）。
9. **单列网格必须 `minmax(0, 1fr)`**：任何 `display: grid` 的单列堆叠容器（折叠体 `.x-body`、内容体 `.x-content`、`.field`、`.sheet-scroll` 等）必须声明 `grid-template-columns: minmax(0, 1fr)`。深层子孙里不可折行的长串（如路径、型号 ID）的 min-content 贡献会穿透 `overflow: hidden` 逐层向上撑破网格轨道，导致内容画出卡片外；文本省略号（`overflow:hidden`+`text-overflow`）管不住这条贡献链（2026-10 技能卡展开溢出实测）。
10. 提交前：build + typecheck + 单测过；`git status` 只提交本任务文件。

## 8. 新功能 UI checklist（按序过一遍）

| 步 | 问题 | 依据 |
| --- | --- | --- |
| 1 | 它属于哪个区域？（顶栏/阅读栏/输入胶囊/抽屉/工作台 Sheet/设置/阅读器/浮层） | 第 3 节 |
| 2 | 用户需要什么状态？（加载/空/错误/成功/进行中） | 第 4 节三态行 |
| 3 | 状态从哪来？后端枚举的中文映射加了吗？ | `statusLabels.ts` |
| 4 | 复用哪个模式？（Collapse/Panel/按钮分级/执行轨迹/报告卡） | 第 4 节 |
| 5 | 样式全用令牌了吗？追加覆盖了吗？ | 第 7.1/7.2 |
| 6 | 键盘/触屏/读屏可用吗？ | 第 6 节 |
| 7 | 需要动效吗？在预算内吗？ | 第 5 节 |
| 8 | build + typecheck + 单测过了吗？需要截图验收吗？ | 第 1 节 |

## 9. 待后端配合清单（前端不越界，需要时先开对应批次）

| 项目 | 需要的改动 | 归属 |
| --- | --- | --- |
| 工具显示元数据 | 已由工具 YAML 提供中文名，状态 API 返回 `tool_catalog`；设置与执行轨迹消费同一来源 | 本仓库 config + bff |
| 股票枚举裸展示（B04/B8 编号等） | observation 直接给中文标签 | 股票仓库 |
| "人工操作"命令行式交互 | 动作化接口（按钮+结构化结果） | 股票仓库 |
| 会话正文搜索 | 已返回命中摘要；搜索只刷新列表，不重载当前会话；可跨工作区搜索 | 本仓库 bff + core |
| 契约字段漂移 | `bff/schemas.py` 与 `types.ts` 对齐或裁剪 | 本仓库 bff |
| 多目录、多会话工作区 | R2 已接入命名工作区、会话归属、稳定目录映射与后端锁；容器运行验收边界见 [R2 记录](./记录/2026-10-07-多目录多会话工作区完善.md) | 本仓库 bff + core + 工具容器 |

## 相关文档

- 批次进度：股票仓库 `docs/计划.md`；主题方案：[深色模式落地计划](./深色模式落地计划.md)；重写记录：[2026-10-07 前端原型 A 落地](./记录/2026-10-07-前端原型A落地.md)
- 协作规则：[../AGENTS.md](../AGENTS.md)、[文档记录简则](./文档记录简则.md)
- 验收方法：[回归验证指南](./回归验证指南.md)

## 工作区与会话（R2）

工作区选择器位于会话抽屉和工作台，两处消费同一个选中状态。默认列表只显示当前工作区会话，搜索跨工作区并显示所属名称及命中摘要。会话历史、模型、方法、草稿、附件独立；工作区共享目录和两级云端授权。生成期间允许起草，禁止切换当前会话、上传和破坏操作；同工作区冲突给出执行会话跳转。目录列表显示用户名称与本机路径，不以虚拟目录 ID 作可见标题。

### 人工HTML简报（2026-10-14）

现有工作区HTML文件报告卡及会话工作区HTML链接可进入同一Reader。外层使用现有石墨黑令牌；已确认的出版物HTML在隔离iframe内保留纸面样式，不向主应用注入CSS。版本、资料日期、人工属性和原研究状态单列；参考默认折叠且编号可展开定位。下载沿工作区原始文件接口；无新公共API。实现与检查见[阅读器接入记录](./记录/2026-10-14-IN4.19-人工简报阅读器接入.md)。

### 研究材料与简报入口

内部 stock_report_read 回执只进入执行轨迹，不自动渲染用户报告卡。brief_delivery 消息独立成组，不继承上一轮失败或工具轨迹，不提供模型重新生成；工作区 HTML 链接使用单一“阅读简报”按钮。原材料按会话所属工作区保存，禁止用当前界面工作区替代会话归属。

独立段落的工作区 HTML 链接使用通用 ArtifactCard（复用 report-card 令牌与响应式布局）；行内引用仍为链接。组件只呈现元数据和动作，类型识别、会话工作区授权、内容隔离由调用方保留。ReportCard 为原接口包装；新增内容类型应先实现实际预览和权限，再复用卡片，不能仅凭扩展名执行内容。

## 插件贡献的施工边界

搜索与金融设置由各自 plugin.json 的 ui.settings 声明，代码归 apps/web/src/plugins/<id>/；宿主 SkillsPanel 只消费构建生成的登记项。CapabilityPanelProps 提供会话只读快照与变更通知，不暴露 App 状态对象。基础卡片仍使用原 skill / Collapse / Switch 与主题令牌。复杂插件面板独立资源包按需载入，局部渲染异常仅显示该插件错误。

npm run build / typecheck / dev 自动生成 extensions/generated.ts；不能手工修改生成文件。登记功能检查：node --test apps/web/scripts/test-extensions.mjs。外部包自动安装及通用 schema 表单仍未完成，不能把源码构建贡献描述为运行中任意 UI 热插拔。
