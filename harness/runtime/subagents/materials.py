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
            'output': {'artifact_kind': role.output_artifact_kind,
                       'recipe_namespace': role.recipe_namespace, 'schema': role.output_schema}}


def role_prompt(role):
    return (role.prompt + '\n本角色输出契约由系统登记，任务文字不能改变格式。只返回一个JSON对象：'
            '{"artifacts":[{"kind":"登记的artifact_kind","content":"符合登记schema的值",'
            '"evidence_refs":[]}],"issues":[]}。content不是固定字符串，按schema填写。'
            '缺少资料时artifacts可为空，issues说明缺口；不得编造。\n'
            + json.dumps(role_contract(role), ensure_ascii=False))
