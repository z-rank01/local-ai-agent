"""Manifest and batch declaration validation without model execution."""
import json
import re
from dataclasses import replace
from harness.contracts.plugin_manifest import read_manifest
from harness.contracts.subagents import RoleSpec, RoleTask


_ROLE_ID = re.compile(r'[a-z][a-z0-9_-]{0,47}')
NON_DELEGATABLE = frozenset({'agent_batch_create', 'agent_batch_status', 'agent_batch_result',
                             'agent_batch_stop', 'local_agent'})


def role_spec(value, *, plugin_id='', plugin_version='', known_tools=(), parent_only=()):
    if not isinstance(value, dict):
        raise ValueError('子Agent角色须为对象')
    role_id = value.get('role_id')
    prompt = value.get('prompt')
    tools = value.get('tools', [])
    depends_on = value.get('depends_on', [])
    output = value.get('output') or {}
    if not isinstance(role_id, str) or not _ROLE_ID.fullmatch(role_id):
        raise ValueError('子Agent角色ID无效')
    if not isinstance(prompt, str) or not prompt.strip():
        raise ValueError('子Agent角色须包含提示指令')
    if (not isinstance(tools, list) or any(not isinstance(tool, str) for tool in tools)
            or len(tools) != len(set(tools))):
        raise ValueError('子Agent工具须为去重后的名称列表')
    if any(not isinstance(parent, str) or not _ROLE_ID.fullmatch(parent) for parent in depends_on):
        raise ValueError('子Agent依赖角色无效')
    optional = value.get('optional_tools', [])
    if not isinstance(optional,list) or any(not isinstance(t,str) or not t for t in optional):
        raise ValueError('optional_tools须为工具名称列表')
    inherit = value.get('inherit_tools',False)
    if not isinstance(inherit,bool):
        raise ValueError('inherit_tools须为布尔值')
    optional_dependencies = value.get('optional_dependencies', False)
    if not isinstance(optional_dependencies, bool):
        raise ValueError('optional_dependencies须为布尔值')
    forbidden = (set(tools) | set(optional)) & (NON_DELEGATABLE | set(parent_only))
    if forbidden:
        raise ValueError('子Agent不能递归委派、创建研究或发布：' + ','.join(sorted(forbidden)))
    unknown = set(tools) - set(known_tools)
    if unknown:
        raise ValueError('子Agent工具超出父运行授权：' + ','.join(sorted(unknown)))
    if (not isinstance(output.get('artifact_kind'), str) or not output['artifact_kind'].strip()
            or not isinstance(output.get('recipe_namespace'), str) or not output['recipe_namespace'].strip()
            or not isinstance(output.get('schema', {}), dict)):
        raise ValueError('子Agent须声明产物类型、Recipe命名空间和输出schema')
    return RoleSpec(role_id=role_id, prompt=prompt, tools=frozenset(tools),
        depends_on=tuple(depends_on), output_artifact_kind=output['artifact_kind'],
        recipe_namespace=output['recipe_namespace'], output_schema=output.get('schema', {}),
        plugin_id=plugin_id, plugin_version=plugin_version, optional_tools=frozenset(optional), inherit_tools=inherit,
        optional_dependencies=optional_dependencies)


def validate_dependencies(roles):
    visiting, visited = set(), set()
    def visit(role_id):
        if role_id in visiting:
            raise ValueError('子Agent角色依赖存在循环')
        if role_id in visited:
            return
        visiting.add(role_id)
        for parent in roles[role_id].depends_on:
            if parent not in roles:
                raise ValueError('子Agent依赖未登记角色：' + parent)
            visit(parent)
        visiting.remove(role_id)
        visited.add(role_id)
    for role_id in roles:
        visit(role_id)


def load_role_specs(packages, known_tools, config_path=None):
    roles = {}
    manifests = [(m,read_manifest(m,packages)) for m in sorted(packages.glob('*/plugin.json'))]
    if config_path is not None and config_path.exists():
        declarations = json.loads(config_path.read_text(encoding='utf-8')).get('agent_roles', [])
        for value in declarations:
            role = role_spec(value, known_tools=known_tools)
            if role.role_id in roles:
                raise ValueError('子Agent角色ID冲突：' + role.role_id)
            roles[role.role_id] = role
    for manifest,metadata in manifests:
        declarations = metadata.get('agent_roles', [])
        if not isinstance(declarations, list):
            raise ValueError('agent_roles须为角色列表')
        for value in declarations:
            if not isinstance(value, dict):
                raise ValueError('子Agent角色须为对象')
            value = dict(value)
            if 'prompt_path' in value:
                path = (manifest.parent / value.pop('prompt_path')).resolve()
                if not path.is_relative_to(manifest.parent.resolve()) or path.suffix not in ('.md','.txt'):
                    raise ValueError('子Agent提示词必须位于当前插件目录内')
                value['prompt'] = path.read_text(encoding='utf-8')
            # A package can restrict its own declarations. Live ownership is checked
            # again by the executor, including for tools supplied by other packages.
            role = role_spec(value, plugin_id=metadata['id'], plugin_version=metadata['version'],
                             known_tools=known_tools, parent_only=metadata.get('tool_roles', {}).get('parent_only', ()))
            if role.role_id in roles:
                raise ValueError('子Agent角色ID冲突：' + role.role_id)
            roles[role.role_id] = role
    validate_dependencies(roles)
    return roles


def role_tasks(items, roles, *, batch_id=''):
    if not isinstance(items, list) or not items:
        raise ValueError('子Agent批次须至少包含一个任务')
    tasks, seen = [], set()
    for item in items:
        if not isinstance(item, dict) or not isinstance(item.get('role_id'), str):
            raise ValueError('子Agent任务须选择已登记role_id')
        role = roles.get(item['role_id'])
        if role is None:
            raise ValueError('未登记的子Agent角色：' + item['role_id'])
        if role.role_id in seen:
            raise ValueError('同一批次角色不能重复：' + role.role_id)
        seen.add(role.role_id)
        text = item.get('task')
        if not isinstance(text, str) or not text.strip():
            raise ValueError('子Agent任务须包含明确task')
        evidence_ids = item.get('evidence_ids', [])
        if not isinstance(evidence_ids, list) or any(not isinstance(key, str) for key in evidence_ids):
            raise ValueError('evidence_ids须为已登记证据编号列表')
        scope = item.get('tool_scope')
        if scope is not None and (not isinstance(scope,list) or any(not isinstance(n,str) for n in scope)):
            raise ValueError('tool_scope须为工具名称数组')
        tasks.append(RoleTask(role=role, text=text, evidence_ids=tuple(evidence_ids), batch_id=batch_id,
                              tool_scope=None if scope is None else frozenset(scope)))
    tasks = [replace(t, role=replace(t.role, depends_on=tuple(p for p in t.role.depends_on if p in seen)))
             if t.role.optional_dependencies else t for t in tasks]
    for task in tasks:
        missing = set(task.role.depends_on) - seen
        if missing:
            raise ValueError('批次缺少依赖角色：' + ','.join(sorted(missing)))
    return tuple(tasks)
