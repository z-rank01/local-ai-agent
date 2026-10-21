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
