"""Structured role outputs and content identity, never hidden reasoning."""
import json
import re
from harness.contracts.subagents import Artifact, RoleResult
from harness.runtime.evidence import fingerprint


def visible_text(value):
    return re.sub(r'<think>.*?(?:</think>|$)', '', value or '', flags=re.S | re.I).strip()


def public(value):
    if isinstance(value, dict):
        return {key:public(item) for key,item in value.items()
                if key not in ('reasoning_content', 'thinking')}
    if isinstance(value, list):
        return [public(item) for item in value]
    return value


def structured_result(task, text, *, status, native_receipt=None, error=''):
    raw = visible_text(text)
    native_receipt = public(native_receipt or {})
    if status != 'COMPLETE':
        return RoleResult(role_id=task.role.role_id, status=status, error=error,
                          native_receipt=native_receipt or {}, raw_text=raw)
    fenced = re.fullmatch(r'```(?:json)?\s*\n(.*?)\n```', raw, flags=re.S | re.I)
    value = json.loads(fenced.group(1) if fenced else raw)
    if not isinstance(value, dict):
        raise ValueError('子Agent结果须为对象')
    items = value.get('artifacts')
    if isinstance(items, dict):
        items = list(items.values())
    if not isinstance(items, list):
        raise ValueError('子Agent结果须包含artifacts数组')
    issues = value.get('issues', [])
    if issues in (None, '', '无', '无。'):
        issues = []
    if not isinstance(issues, list) or any(not isinstance(issue, (dict, str)) for issue in issues):
        raise ValueError('子Agent issues须为对象或文字数组')
    issues = [{'reason':issue} if isinstance(issue, str) else issue for issue in issues]
    artifacts = []
    for item in items:
        if not isinstance(item, dict) or item.get('kind') != task.role.output_artifact_kind:
            raise ValueError('子Agent产物类型与角色声明不符')
        if 'content' not in item:
            raise ValueError('子Agent产物缺少content')
        refs = item.get('evidence_refs', [])
        if not isinstance(refs, list) or any(not isinstance(ref, str) or not ref for ref in refs):
            raise ValueError('子Agent证据引用须为字符串数组')
        content = public(item['content'])
        _validate_schema(content, task.role.output_schema)
        artifacts.append(Artifact(kind=item['kind'], content=content,
            producer_role=task.role.role_id, sha256=fingerprint(content), evidence_refs=tuple(refs)))
    return RoleResult(role_id=task.role.role_id, status=status, artifacts=tuple(artifacts),
        issues=tuple(public(issues)), native_receipt=native_receipt or {}, raw_text=raw)


def _validate_schema(value, schema):
    expected = schema.get('type')
    types = {'object': dict, 'array': list, 'string': str, 'number': (int, float), 'boolean': bool}
    if expected in types and not isinstance(value, types[expected]):
        raise ValueError('子Agent产物content须为' + expected)
    missing = [key for key in schema.get('required', []) if key not in value]
    if missing:
        raise ValueError('子Agent产物缺少字段：' + ','.join(missing))
    if isinstance(value, dict):
        for key, child in schema.get('properties', {}).items():
            if key in value:
                _validate_schema(value[key], child)
    if isinstance(value, list) and isinstance(schema.get('items'), dict):
        for item in value:
            _validate_schema(item, schema['items'])
