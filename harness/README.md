# 能力接入契约

本文说明仓库已经提供的接口与兼容边界。交付进度只维护股票仓库 `docs/计划.md`；接口存在不代表整套重构或真实模型验收通过。

## 内容 Skill

分发目录使用 `SKILL.md`，可带 `scripts/`、`references/`、`assets/`。正文前置 YAML 至少提供 `name` 与有实际适用范围的 `description`。可以直接维护现有工作区技能目录，也可通过工作台导入已挂载目录。

运行开始前冻结内容版本；`skill_info` 返回该运行固定的正文和资源位置。执行脚本时使用回执中的版本路径，不能自行改回当前源目录。新增权限或依赖不会因修改文件自动获准。停用、回退和卸载不删除用户成果。

可参考 Runner 的 `packages/meeting-notes` 与 `packages/table-reconcile`。目前旧 Python Skill 仍走兼容执行；版本目录的内容校验不能替代操作系统层面的不可写隔离。

## 文件服务内的工具 Plugin

当前可运行示例为 `plugins/document-index/`：

```text
plugins/<id>/
  plugin.json
  tools/*.yaml
  runtime/provider.py
```

清单声明 `harness_api: 1`、稳定 ID、版本、依赖、权限和贡献项。文件服务执行包声明 `execution: "files"`、`tool_registration: "managed"`；工具的传输后端为 `skill-files`，工具所有者为插件 ID。运行类实现：

```python
class Provider:
    def invoke(self, tool, arguments, context):
        import hashlib
        source = context.resolve_path(arguments['path'])
        raw = source.read_bytes()
        return {'status': 'COMPLETE', 'path': str(source),
                'bytes': len(raw), 'sha256': hashlib.sha256(raw).hexdigest()}
```

`ExecutionContext` 来自对应工作区服务，插件不能自行构造另一工作区的授权。代码只在已构建镜像内延迟加载；宿主激活前核验资源哈希、版本和已有鉴权接口。源码更新使用唯一入口 `启动.bat` 对应的 `start-daily.ps1 -Build` 部署。

这是显式随源码安装的执行机制。外部目录/压缩包的完整安装、信任确认、环境准备和版本回退尚不能靠直接复制到扫描目录代替。

## 服务与任务

工具登记使用 `replace_owner` / `unregister_owner`，服务传输通过 `ToolRouter.bind_provider` 的 `ToolTransport` 契约接入。不要改写其他模块的私有字典。生命周期实现 `activate` 与 `deactivate`，由统一管理器控制依赖顺序、调用准入、在途等待和失败状态。

任务由所属插件保管事实，`TaskProvider` 只投影 `list`、`get`、`control`；宿主按命名空间路由，不复制任务数据库。任务行返回实际状态和 `actions`，不得以进程在线推断任务完成。金融提供者仍保留整轮聊天切换保护，尚不能把逐工具调用的租约等同于整轮会话收尾。

## 模型网关

宿主用冻结的会话模型与工作区授权建立 `ConversationModelGateway(runtime, execution_profile)`，向能力提供绑定后的网关。请求本身没有模型选择字段：

```python
from harness.contracts.models import ModelRequest

receipt = await gateway.generate(ModelRequest(
    request_id='plugin-id:' + saved_attempt_id,
    prompt='核对明确给定的材料，区分事实与缺口。',
    material=authorized_material,
))
```

`saved_attempt_id` 必须来自宿主或任务所有者已经保存的本次调用标识。网络重连不能重新生成标识；明确批准的新尝试使用自己的新标识。网关在原 `model-calls.sqlite` 中原子占用请求，不新建账本。重复 ID 拒绝再次外发，变更输入也不能复用同一 ID；UNKNOWN 不自动重发。

账本只保存调用元数据和终态，不保存正文。第一次收到结果时，能力所有者负责保存成果；重复调用不能从账本伪造或恢复未保存的正文。旧入口未提供稳定节点标识时仍有兼容路径，不能据此声称所有模型调用已完成统一迁移。

## 前端设置贡献

插件清单声明：

```json
{"ui": {"settings": "apps/web/src/plugins/example/index.tsx", "title": "示例", "description": "说明适用任务", "order": 30}}
```

模块默认导出接收 `CapabilityPanelProps` 的组件。只通过会话快照、变更回调、通用 HTTP 接口及公共基础组件协作，不读取 App 私有状态。`npm run build --prefix apps/web` 自动生成登记，独立资源按需载入；不用手工修改宿主导入清单，不进行任意远程 JavaScript 热加载。

普通 schema 表单、命令、成果渲染器以及外部包安装后的资源构建仍需继续实现。现有设置贡献不能代表全部 UIContribution 范围。


## 能力提示贡献

仅已信任、随源码部署的插件可以在清单声明 `prompts`，例如：

```json
{"prompts": [{"path": "prompts/usage.txt", "when_tools": ["documents_find"]}]}
```

文件必须位于自身包内且为 UTF-8 文本，最多 128 KiB。任一声明工具已登记时装配提示；停用全部相关工具后不再加入。该条件只表示能力存在，不能扩大模型或工作区权限。加载失败隔离到对应包并记录错误。当前外部包导入仍不授予系统提示权限。

运行时启动时读取提示；更新使用原入口部署。文件内容与适用工具声明加入检查点指纹，不静默继续规则不同的历史运行。公开研究工具尚在通用宿主，因此金融提示在该工具存在时仍可加载；这不代表金融执行逻辑已经完全迁出。


## 已保存成果与提供者分页

工具回执中的 `local_result` 仍保存在原消息记录。宿主先核验会话归属和有效记录，再构造 `SavedArtifact`。`GET /api/conversations/{id}/artifacts/{message_id}` 返回已保存正文、内容哈希修订号和 `conversation-local-ui` 可见范围；不把内部参数返回页面，也不把片段完整度猜成 COMPLETE。传入旧 `revision` 时返回 409，不替换为另一个版本。

插件声明 `artifacts: {provider, owner, tools}`，实现 `ArtifactProvider.read_page(saved, request)`。提供者只取得已授权记录和自身声明的已登记传输；金融包负责原任务引用、分页与业务修订冲突。旧 report-page 页面接口沿通用提供者路由，保存正文的读取不依赖插件在线。完整成果类型、历史全部修订保留以及前端自定义成果渲染贡献仍待继续；不能将这一步等同于全部 ArtifactProvider 验收。


Skill 的运行快照摘要通过宿主的 `tool_descriptions` 注入本轮 `skill_info` 工具说明，正文仍由实际调用按需读取。说明不改 schema、工具登记或权限，也不改共享注册表；新包不需要新增工具或 Agent 分支。仅摘要进入选择面不代表模型必然选择，必须保留真实调用与成果验收。


需要运行上下文的传输实现 `ContextualTransport.bind(context)`，返回独立的 ToolTransport 绑定。宿主提供会话/运行 ID、冻结工作区解析器、模型 ID、权限集合以及已绑定的 ModelGateway；插件不能从请求参数覆盖这些值。普通文件服务上下文仍可只使用工作区字段。两个运行不共享可变绑定，原已登记传输不被改写；现有金融兼容桥暂保留原会话路由，尚未转换为这一绑定形式。


## 可选容器服务

清单 `execution: container` 声明固定 `service`、`services` 资源、工具目录与包内 `transport`。宿主只向唯一启动器发送插件/服务/工作区 ID；不能传命令。准备环境与激活分开，准备在启动器进程内后台构建，页面轮询原能力状态；退出取消未确认构建。工作区绑定保存在原 conversations.db，镜像与正在运行的容器分别核验资源哈希和工作区身份。未准备不能激活，首次工具调用才启动所属工作区容器。

容器传输实现 `bind(ExecutionContext)`，业务参数不能覆盖会话、模型、工作区和授权。任务提供者可声明 `task_service`，由宿主注入窄的 ContainerClient；事实仍来自工作区服务，不复制任务库。停用先拒绝新调用并等待调用返回，再请求后台任务收尾、停止容器。退出沿同一路径，不能用旧源码哈希拒绝已鉴权旧容器的正常收尾。

批量文档包只处理明确列出的 PDF/图片，最多 50 个、每个 100 MiB；中文/英文 OCR 环境可选，默认不启动。每个任务在工作区保存一份 JSON 回执，输出在 `reports/document-batch/<任务ID>/`，原件不写入。单文件失败保留其他成果，重启时未确认文件保持 UNKNOWN，任务 INTERRUPTED；不自动重试。文本从全部输出 PDF 页面提取，不能把 OCR sidecar 当全文。实际效果、耗时及镜像体积须另做真实验收。

Docker Desktop 下内部网络不保证发布主机端口，因此可选服务配套固定上游 Python 代理。处理容器保留 internal 网络；代理只挂载宿主固定转发代码，使用已准备镜像，64 MiB 上限，无工作区挂载及静态凭据。主机端口只绑定 127.0.0.1，所有实际请求仍由处理容器校验原工作区令牌。该代理没有任意 URL 或 CONNECT 接口。


生命周期按清单 ID 登记；迁移中的旧工具所有者通过 `register(..., aliases=...)` 映射到同一实例。依赖可以使用清单 ID，旧传输的调用计数、拒绝新调用与停用门禁不会另建一份状态。别名冲突、自依赖及跨别名循环均拒绝；宿主不得用多次注册制造并行生命周期。


### 旧式 Python Skill 版本兼容（2026-10-21）

`skills/<name>.py` 只作兼容形态；新建仍使用 `SKILL.md + scripts`。旧文件按 AST 读取元数据、按内容哈希保存源码快照，与方法包共用原会话库中的启停、待激活和历史记录。宿主在运行开始时固定版本，通过受鉴权请求头交给原 Runner；模型参数不能改版本，停用或版本服务缺失不能回退执行可变源文件。执行和取消复用原流式进程组机制。

发现、注册、修改均不导入技能代码或自动安装依赖。新增权限/环境依赖待检查，依赖准备仍是单独操作；执行前复核实际 Python 环境。首版只固定单文件源码，不冻结其任意外部导入和数据文件；需要多文件资源请迁为标准目录包。直接 URL 依赖与 extras 自动核验不支持，错误明确展示。删除源文件保留已引用快照，卸载不删除用户成果。工具容器改动必须通过唯一入口 `start-daily.ps1 -Build` 生效。
