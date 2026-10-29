# 前端设计与交互摘要（2026-10-29）

本文件是前端视觉、交互、施工与计划的**唯一摘要**，替代此前的 `UI设计笔记`、能力栏 A 规范、能力管理 rollout plan、深色模式计划和原型 README。完整原稿保留在 git 历史（压缩前提交 `a3fb1db`）；批次进度只维护股票仓库 `docs/计划.md`，详细证据在[记录](./记录/README.md)。

## 1. 基线与令牌

- 视觉沿用 Apple 设计系统；`styles.css :root` 是唯一颜色/字号/间距/圆角/动效来源。新增样式只引用令牌，不写裸 hex，不加新依赖/远程资源。
- 字体本地化；正文 `--text-sm` 14、辅助 `--text-xs` 12、标题 `--text-base` 17/`--text-lg` 21；代码使用 `--code-font`。
- 表面：`--bg` 页面、`--surface` 面板、`--surface-warm` 组/输入、`--card` 浮层、`--fg`/`--fg-2`/`--muted`/`--meta` 文字阶梯。
- 语义色只用 `--accent`/`--link`/`--success`/`--warn`/`--danger` 及其派生 soft/text；焦点 `--focus-ring` 4px，搜索等紧凑控件 `--focus-ring-sm` 2px。
- 圆角 `--radius-sm/md/lg/pill`；动效 `--motion-fast` 150ms、`--motion-base` 220ms、`--ease-standard`。

## 2. 布局与组件

| 区域 | 规则 |
| --- | --- |
| 顶栏 | 44px，细条；会话标题居中；模型/工作台/设置/导出入口靠右；窄屏用 container query 收起文字 |
| 会话抽屉 | 左 322px；新建、搜索、会话列表、任务卡、唯一「退出」按钮 |
| 工作台 Sheet | 右侧固定，默认 400px，可拖 340–760px；Sheet head + 可折叠 Panel；窗口剩余 <560px 时改为覆盖式 |
| Composer | 底部悬浮胶囊；运行状态行、A 能力栏、附件、输入、模型/发送排布；窄屏不横向滚动 |
| 基元 | `Panel` 折叠、`Collapse` grid 折叠、`Popover` portal/焦点回退、`Switch` role=switch、`Chip/Badge`、`Button/icon` |
| 页面模式 | 列表用单张分组卡片+行分隔，不用每项独立浮卡；详情就地展开；浮层不裁剪 |

响应式：320 / 768 / 1440 必测；Sheet 内部优先 container query（而不是只看 viewport）；触屏粗指针控件至少 44px 命中；200% 缩放按等效窄宽核对。

## 3. 动效与可访问性

- 入场 `rise` 8px/220ms；浮层 `pop-in`/`fade` 150ms；持续只用 `dots`/`spin`/`pulse-ring`；不新增弹簧或高度动画。
- **居中固定元素（toast、notice-bar）必须用 `centered-rise`**：关键帧同时保留 `translateX(-50%)`，否则 `rise` 的 `transform` 会在入场时覆盖居中，先偏右再跳回。
- 文件末尾全局 reduced-motion 1ms + 单次；新动画必须被覆盖。
- 焦点：统一 `:focus-visible`；禁止裸删 outline；弹层 Esc 关闭并回触发按钮；打开移入首个可聚焦元素；行展开/收起保留焦点；Switch 操作后焦点回同一开关。
- 语义：图标按钮有 aria-label；弹层 `role=dialog`；筛选 `aria-pressed`/`aria-controls`；列表 `role=list` + 行 `role=listitem`；状态不只靠颜色。

## 4. 深色模式

- 三态：跟随系统 / 浅色 / 深色；默认与重置为跟随系统；存储 `local-ai-agent.appearance.v1`；无效值回退系统。
- 深色使用纯黑舞台与石墨黑层级；浅色既有配色和布局不改；代码卡、轨迹、阅读器保持深色。
- 初始化在 CSS/React 前解析并写根元素主题，防闪烁；运行期 `lib/theme.ts` 监听系统，手动态不跟随；卸载清理监听。
- 验收：24 项主题测试、typecheck/build；六档宽度和实际计算样式检查。原生 `-Desktop`、真实系统主题变化、首帧录制仍未自动验收。

## 5. 能力栏 A

- Composer 内固定 40px 单行（触屏 52）；最多 2 枚已选胶囊 + `+N`，右侧唯一「能力」触发器；浮层 374px、portal、空间不足上翻。
- 浮层含搜索、全部/技能/插件筛选、行状态、加载/空/无结果/错误/不可用；管理入口跳工作台能力面板。
- 选择是会话级 `conversation.capability_ids`（v11，`skill:<name>` / `plugin:<id>`）；**空选择不缩窄**，非空只收窄本轮 Skill/Plugin 发现目录，不改工具 schema/权限/生命周期。
- 键盘：打开聚焦搜索、Tab 循环、↑↓、Esc 回触发器、Ctrl/Cmd+K 开合、移除胶囊后焦点下一项或触发器；搜索 2px 环，input 不叠全局环。
- 响应式：≤540 隐藏快捷键；≤380 胶囊限宽 124；≤340 触发器图标化；320 无横向滚动。
- 本地最近使用最多 3；搜索本地；无遥测管线，不伪造事件；关闭沿用共享浮层立即卸载（不做 180ms 关闭动画）。
- 构建开关 `VITE_CAPABILITY_BAR_A` 默认 on；off 同时回退 Composer 与侧栏。

## 6. 能力管理侧边栏 v2

- 位置：Sheet → Panel「能力」。面板内是 hero（工作区 · 能力 / 能力管理 / 副标题 + 导入/刷新）、搜索、类型筛选+计数、启用汇总、单张能力组、页脚统计与「显示已移除」。
- 行：32px 图标、display name、`对话中`/状态 badge、两行描述、`技能/插件 · source · origin`、Switch、更多菜单、展开 chevron；展开保留概览/设置/文件/版本。
- 状态分工：**侧栏 Switch 只管 `enabled`，Composer 胶囊只管 `selected`**；正常启用/停用不堆 chip，warn/danger 才显示状态 badge。
- 停用一个已选能力：API 接受后立即从当前会话移除，Toast「撤销」重新启用并恢复选择；切会话/工作区清掉旧撤销；运行中的能力按后端边界完成当前运行。
- 无效 Skill 单独错误块；已移除历史放页脚；空库/无结果/加载/错误分开。
- 响应式：`.capm` 用 container query；≤360 收紧行/页脚，≤310 隐藏启用汇总，≤300 Switch/菜单纵向堆叠；320 无横向滚动。
- 复用 `VITE_CAPABILITY_BAR_A` 作为能力面总开关；off 时用 `SkillsPanelLegacy`，新 manager 不渲染。无新后端/迁移/双写/遥测；真实 Skill/Plugin toggle 需用户自行操作，审计使用拦截 API。

## 7. 原型与证据

| 原型 | 用途 |
| --- | --- |
| [深色模式原型](./原型/harness-a-dark-mode.html) | 深色主题视觉来源；演示数据 |
| [能力栏 A 定稿原型](./原型/capability-bar-review.html) | A 栏与浮层视觉/交互；B/C 仅参考；演示数据 |
| [能力管理侧边栏原型](./原型/capability-manager.html) | 右侧能力管理 hero/列表/开关参考；演示数据 |

原型只用于设计对照，不连接真实后端；日常入口仍是根目录 `启动.bat`。实现证据：

- [深色模式实施记录](./记录/2026-10-10-深色模式.md)
- [能力栏 A 落地](./记录/2026-10-29-能力栏A落地.md)
- [能力管理侧边栏 v2](./记录/2026-10-29-能力管理侧边栏视觉更新.md)
- [前端原型 A 落地](./记录/2026-10-07-前端原型A落地.md)

## 8. 施工与验收

- 动手前读根目录 [AGENTS.md](../AGENTS.md) 与[文档记录简则](./文档记录简则.md)；改前端先看本摘要；进度只写股票仓库计划。
- 新样式先用令牌；缺令牌先补 `:root`；不新增依赖；不启第二个服务；唯一入口 `启动.bat`。
- 最小验证：`npm run test:capability-bar`、`npm run test:theme`、`npm run typecheck`、`npm run build`；涉及选择/启停再跑 `tests.test_capability_selection`；涉及能力切换跑目标页面。
- 页面验收：320/768/1440、浅/深色、键盘、焦点、触屏、reduced motion；能力管理还要验证停用已选→Composer 移除→撤销，以及 flag off 旧界面。
- 证据写日期、操作、实际结果与未覆盖项；不把构建/自动测试冒充真实模型或用户验收。
