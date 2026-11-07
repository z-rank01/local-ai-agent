"""A mutable research work order; receipts remain executor-owned, not model claims."""
from copy import deepcopy
from uuid import uuid4

FIELDS = {'name', 'goal', 'depends_on', 'input_refs', 'expected_outputs', 'checks',
          'output_refs', 'open_questions', 'assessment', 'model_status', 'claims', 'supersedes','required_roles'}


def update_recipe(current, initial, updates, plan, artifacts, *, version=2):
    recipe = deepcopy(current) if current else {'schema_version': version, 'revision': 0, 'stages': [],
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
        if version == 3 and 'claims' in patch:
            raise ValueError('v3判断由分析角色填写；作者用decision_claim_ids选择，不改写角色成果')
        identifier = patch.get('id')
        stage = next((s for s in recipe['stages'] if s['id'] == identifier), None)
        if identifier and stage is None:
            raise ValueError('阶段ID未登记；新增阶段省略id，由程序分配')
        if stage is not None and stage.get('owner') == 'subagent':
            raise ValueError('角色原始成果只能由对应角色复核更新，作者不能覆盖')
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
            if key == 'required_roles' and set(value) - {'finance_evidence','finance_calculation','finance_analysis','finance_review'}:
                raise ValueError('工作单需要的金融角色未登记')
            if key == 'claims':
                value = deepcopy(value)
                old_claims = stage.get('claims', [])
                for claim in value:
                    if not isinstance(claim, dict) or set(claim) - {
                            'id', 'text', 'kind', 'entity', 'metric', 'period', 'unit', 'value',
                            'evidence_refs', 'conditions', 'counterevidence', 'unresolved'}:
                        raise ValueError('claims只记录陈述、指标口径和证据引用')
                    if not isinstance(claim.get('text'), str) or not claim['text'].strip():
                        raise ValueError('claim缺少text')
                    refs = claim.get('evidence_refs', [])
                    if not isinstance(refs, list) or any(not isinstance(r, str) or r not in artifacts for r in refs):
                        raise ValueError('claim证据须引用实际artifacts')
                    if any(not isinstance(v, str) for k, v in claim.items() if k != 'evidence_refs'):
                        raise ValueError('claim指标口径须为文字，不构成事实认证')
                    if claim.get('kind', 'inference') not in ('fact','calculation','inference'):
                        raise ValueError('claim.kind须为fact、calculation或inference')
                    if claim.get('id') and claim['id'] not in {c.get('id') for c in old_claims}:
                        raise ValueError('claim ID须来自当前阶段；新增省略ID')
                    prior = next((c for c in old_claims if c['text'] == claim['text']), {})
                    claim.setdefault('id', prior.get('id') or 'claim_' + uuid4().hex)
                if len({c['id'] for c in value}) != len(value):
                    raise ValueError('claim ID不能重复')
            stage[key] = deepcopy(value)
    ids = {stage['id'] for stage in recipe['stages']}
    for stage in recipe['stages']:
        if any(ref not in ids or ref == stage['id'] for ref in
               stage.get('depends_on', []) + stage.get('supersedes', [])):
            raise ValueError('depends_on须引用其他已登记阶段')
    if (changes or updates) and recipe != current:
        recipe['revision'] += 1
    return recipe


def claim_materials(recipe, artifacts, candidate=None):
    """Navigation into actual artifacts, never a substitute for source bodies."""
    from ..publication import literal_text
    body = literal_text((candidate or {}).get('markdown', ''))
    return [{'stage_id':stage['id'], 'claim':deepcopy(claim),
             'outcome_version':outcome_version(stage, artifacts, candidate),
             'body_reference_observed':bool(body and (claim.get('id', '\0') in body or
                                                     literal_text(claim['text']) in body)),
             'semantic_certification':False,
             'materials':[{ 'ref':ref, 'artifact_location':'artifacts',
                            'missing':ref not in artifacts}
                          for ref in claim.get('evidence_refs', [])]}
            for stage in (recipe or {}).get('stages', []) for claim in stage.get('claims', [])]


def candidate_content_fingerprint(candidate):
    from harness.runtime.evidence import fingerprint
    return fingerprint({k:(candidate or {}).get(k) for k in ('title','markdown','as_of','stance')})


def outcome_version(stage, artifacts, candidate):
    refs = [ref for ref in stage.get('output_refs', []) if artifacts.get(ref, {}).get('kind') == 'candidate']
    current = candidate_content_fingerprint(candidate) if candidate else None
    matching = [ref for ref in refs if current and artifacts[ref].get('content_sha256') == current]
    state = 'CURRENT_TEXT_REFERENCE' if matching else 'OLDER_OR_UNVERIFIED_CANDIDATE' if refs and current else 'UNBOUND'
    return {'status':state,'adopted_candidate_refs':refs,'matching_refs':matching,
            'reading':'仅观察采用的稿件正文版本；旧评估不是当前稿事实，引用匹配也不认证判断。'}


def recipe_advice(recipe, artifacts=None, candidate=None):
    result = []
    for stage in (recipe or {}).get('stages', []):
        missing = [key for key in ('assessment','output_refs','claims') if not stage.get(key)]
        version = outcome_version(stage, artifacts or {}, candidate)
        stale = version['status'] == 'OLDER_OR_UNVERIFIED_CANDIDATE'
        if stage.get('owner') != 'subagent' and stage.get('model_status') != 'skipped' and (missing or stale):
            result.append({'stage_id':stage['id'],'missing':missing,'outcome_version':version,
                           'next':'按当前证据更新判断和实际采用的稿件引用；旧版保留，不必逐工具填表，不阻止发布。'})
    return result


def recipe_view(recipe, artifacts, candidate):
    if recipe is None:
        return None
    view = {key:deepcopy(value) for key,value in recipe.items()
            if key not in ('execution','active_stage_id')}
    for stage in view.get('stages', []):
        if stage.get('owner') != 'subagent':
            stage['outcome_version'] = outcome_version(stage, artifacts, candidate)
    return view
