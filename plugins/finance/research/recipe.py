"""A mutable research work order; receipts remain executor-owned, not model claims."""
from copy import deepcopy
from uuid import uuid4

FIELDS = {'name', 'goal', 'depends_on', 'input_refs', 'expected_outputs', 'checks',
          'output_refs', 'open_questions', 'assessment', 'model_status', 'claims'}


def update_recipe(current, initial, updates, plan, artifacts):
    recipe = deepcopy(current) if current else {'schema_version': 1, 'revision': 0, 'stages': [],
                                               'execution': [], 'active_stage_id':None}
    if initial is not None and current:
        raise ValueError('已有Recipe请用stage_updates调整，不能覆盖历史工作单')
    if initial is not None and (not isinstance(initial, dict) or set(initial) != {'stages'}):
        raise ValueError('recipe只接受stages；版本、ID和回执由程序登记')
    changes = (initial or {}).get('stages', []) if not current else []
    if not current and initial is None:
        changes = [{'goal': text} for text in plan]
    if not isinstance(changes, list) or not isinstance(updates, list):
        raise ValueError('stages和stage_updates须为列表')
    for patch in changes + updates:
        if not isinstance(patch, dict) or set(patch) - FIELDS - {'id'}:
            raise ValueError('阶段只接受研究意图、材料引用和模型判断；不能写入执行回执')
        identifier = patch.get('id')
        stage = next((s for s in recipe['stages'] if s['id'] == identifier), None)
        if identifier and stage is None:
            raise ValueError('阶段ID未登记；新增阶段省略id，由程序分配')
        if stage is None:
            if not isinstance(patch.get('goal'), str) or not patch['goal'].strip():
                raise ValueError('新增阶段需要goal')
            stage = {'id': 'stage_' + uuid4().hex, 'goal': patch['goal'],
                     'model_status': 'planned', 'claims': [], 'open_questions': []}
            recipe['stages'].append(stage)
        for key, value in patch.items():
            if key == 'id':
                continue
            if key in {'goal', 'name', 'assessment', 'model_status'}:
                if not isinstance(value, str) or not value.strip():
                    raise ValueError(key + '须为非空文字')
            elif not isinstance(value, list):
                raise ValueError(key + '须为列表')
            elif key != 'claims' and any(not isinstance(v, str) or not v.strip() for v in value):
                raise ValueError(key + '须由非空文字组成')
            if key in {'input_refs', 'output_refs'} and any(ref not in artifacts for ref in value):
                raise ValueError('材料引用必须来自返回的artifacts索引')
            if key == 'claims':
                for claim in value:
                    if not isinstance(claim, dict) or set(claim) - {
                            'text', 'entity', 'metric', 'period', 'unit', 'value', 'evidence_refs'}:
                        raise ValueError('claims只记录陈述、指标口径和证据引用')
                    if not isinstance(claim.get('text'), str) or not claim['text'].strip():
                        raise ValueError('claim缺少text')
                    refs = claim.get('evidence_refs', [])
                    if not isinstance(refs, list) or any(not isinstance(r, str) or r not in artifacts for r in refs):
                        raise ValueError('claim证据须引用实际artifacts')
                    if any(not isinstance(v, str) for k, v in claim.items() if k != 'evidence_refs'):
                        raise ValueError('claim指标口径须为文字，不构成事实认证')
            stage[key] = deepcopy(value)
    ids = {stage['id'] for stage in recipe['stages']}
    for stage in recipe['stages']:
        if any(ref not in ids or ref == stage['id'] for ref in stage.get('depends_on', [])):
            raise ValueError('depends_on须引用其他已登记阶段')
    if changes or updates:
        recipe['revision'] += 1
    return recipe
