"""Complete authorized evidence reads and one shared role output contract."""
import json


def read_material(reader, evidence_id):
    page = reader(evidence_id)
    chunks = [page.get('content', '')]
    cursor = page.get('next_cursor')
    seen = {0}
    while cursor is not None:
        if not isinstance(cursor, int) or cursor in seen:
            raise ValueError('证据分页游标无效：' + evidence_id)
        seen.add(cursor)
        next_page = reader(evidence_id, cursor)
        chunks.append(next_page.get('content', ''))
        cursor = next_page.get('next_cursor')
    return {**page, 'content': ''.join(chunks), 'next_cursor': None}


def role_contract(role):
    return {'role_id': role.role_id, 'purpose': role.prompt.splitlines()[0],
            'tools': sorted(role.tools), 'depends_on': list(role.depends_on),
            **({'optional_tools':sorted(role.optional_tools),'inherit_tools':True} if role.inherit_tools else
               {'optional_tools': sorted(role.optional_tools)} if role.optional_tools else {}),
            'output': {'artifact_kind': role.output_artifact_kind,
                       'recipe_namespace': role.recipe_namespace, 'schema': role.output_schema}}


def role_prompt(role):
    schema = role.output_schema
    required = schema.get('required', [])
    shape = {'kind':role.output_artifact_kind, 'content':'按登记schema填写', 'evidence_refs':[]}
    if schema.get('type') == 'object' and all(
            schema.get('properties', {}).get(key, {}).get('type') == 'array' for key in required):
        shape['content'] = {key:[] for key in required}
    envelope = json.dumps({'artifacts':[shape], 'issues':[]}, ensure_ascii=False)
    return (role.prompt + '\n本角色输出契约由系统登记，任务文字不能改变格式。只返回一个JSON对象：'
            + envelope + '。这是外层结构示意；将实际成果放在artifacts[].content内，按登记schema填写。'
            '缺少资料时artifacts可为空，issues说明缺口；不得编造。\n'
            + json.dumps(role_contract(role), ensure_ascii=False))
