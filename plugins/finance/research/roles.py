"""Map completed finance roles into Recipe artifacts; never rewrite their content."""
from copy import deepcopy
from decimal import DecimalException

from harness.runtime.evidence import fingerprint
from ..publication import literal_text, validate_publication


ROLE_NAMESPACES = {
    'finance_evidence': 'evidence',
    'finance_calculation': 'calculations',
    'finance_analysis': 'analysis',
    'finance_review': 'review',
}
CLAIM_FIELDS = ('text', 'kind', 'entity', 'metric', 'period', 'unit', 'value',
                'conditions', 'counterevidence', 'unresolved')


def _save_recipe(record, previous):
    if previous != record['recipe']:
        record.setdefault('recipe_history', []).append(previous)
        record['recipe']['revision'] += 1


def _excerpt(row, session):
    if not isinstance(row, dict):
        raise ValueError('摘录须为对象')
    source_id, quote = row.get('source_id'), row.get('quote')
    if not isinstance(source_id, str) or source_id not in session.sources:
        raise ValueError('source_id未在当前研究登记')
    if not isinstance(quote, str) or not quote.strip():
        raise ValueError('quote须为非空连续原句')
    if not any(literal_text(quote) in literal_text(text)
               for text in session.read_text.get(source_id, [])):
        raise ValueError('引文不在已登记的实际读取材料中；请核对原句，或通过原读取工具返回完整source_excerpts回执，不以保存文件或裸stdout代替读取登记')
    result = {'source_id':source_id, 'quote':quote}
    verified = next((excerpt for excerpt in session.excerpts.get(source_id, [])
                     if excerpt['sha256'] == session.sources[source_id]['sha256']
                     and literal_text(excerpt['quote']) == literal_text(quote)), None)
    if verified is not None:
        result.update({key:verified[key] for key in ('page', 'start', 'end')})
        result['locator'] = f"第{verified['page']}页，字符{verified['start']}—{verified['end']}"
    else:
        result['locator'] = '原文已收到；页码与字符定位未由程序确认'
        if isinstance(row.get('locator'), str):
            result['declared_locator'] = row['locator']
    return result


def _aliases(record, batch_id):
    return record.setdefault('role_aliases', {}).setdefault(batch_id, {})


def _validate_candidate(session, calculations, figures, aliases):
    if not isinstance(calculations, list) or not isinstance(figures, list):
        raise ValueError('计算和图表须为数组')
    if not calculations and not figures:
        return {'calculations': [], 'charts': []}
    sources, cited = [], set()
    for calculation in calculations:
        for reference in calculation.get('evidence_refs', []):
            ref = aliases.get(reference, reference)
            artifact = session.record['artifacts'].get(ref, {})
            excerpt = artifact.get('excerpt', {})
            source_id = excerpt.get('source_id')
            if artifact.get('kind') == 'source_excerpt' and (source_id, excerpt['quote']) not in cited:
                sources.append({'id': source_id, 'quote': excerpt['quote'],
                                'locator': excerpt['locator']})
                cited.add((source_id, excerpt['quote']))
    for figure in figures:
        for item in figure.get('items', []):
            source_id = item.get('source')
            if any(row['id'] == source_id for row in sources) or source_id not in session.sources:
                continue
            if 'calculation' in item:
                continue
            value = item.get('value')
            if not isinstance(value, str) or not value:
                continue
            excerpt = next((artifact['excerpt'] for artifact in session.artifacts().values()
                            if artifact.get('kind') == 'source_excerpt'
                            and artifact['excerpt']['source_id'] == source_id
                            and value in artifact['excerpt']['quote']), None)
            if excerpt is not None:
                sources.append({'id': source_id, 'quote': excerpt['quote'],
                                'locator': excerpt['locator']})
                cited.add((source_id, excerpt['quote']))
    candidate = {'title': '角色计算', 'markdown': '待作者选择', 'as_of': '以材料为准',
                 'stance': '等待验证', 'sources': sources, 'calculations': calculations,
                 'charts': figures}
    return validate_publication(candidate, session.sources, session.read_text)


def _validate_calculations(session, content, aliases):
    calculations, errors, indices = [], [], []
    compact = []

    def remap_inputs(calculation):
        value = deepcopy(calculation)
        for item in value.get('inputs', []):
            source = item.get('source', '')
            if source.startswith('calculation:'):
                ref = source.removeprefix('calculation:')
                if not ref.isdecimal() or int(ref) not in indices:
                    raise ValueError('计算依赖须引用此前已校验的 calculation:零起始索引：' + source)
                item['source'] = 'calculation:' + str(indices.index(int(ref)))
        return value

    for index, calculation in enumerate(content['calculations']):
        try:
            candidate = remap_inputs(calculation)
            checked = _validate_candidate(session, compact + [candidate], [], aliases)
            validated = checked['calculations'][-1]
            compact.append(validated)
            original_indices = deepcopy(validated)
            for item in original_indices['inputs']:
                if item['source'].startswith('calculation:'):
                    item['source'] = 'calculation:' + str(indices[int(item['source'].split(':')[1])])
            calculations.append(original_indices)
            indices.append(index)
        except (ValueError, KeyError, TypeError, DecimalException) as error:
            errors.append({'index':index, 'error':str(error)})
    charts, chart_indices = [], []
    for index, figure in enumerate(content['figures']):
        try:
            candidate = deepcopy(figure)
            for item in candidate.get('items', []):
                if 'calculation' in item:
                    ref = item['calculation']
                    if type(ref) is not int or ref not in indices:
                        raise ValueError('图表引用了未通过校验的计算：' + str(ref))
                    item['calculation'] = indices.index(ref)
            checked = _validate_candidate(session, compact, [candidate], aliases)['charts'][0]
            for item in checked['items']:
                if 'calculation' in item:
                    item['calculation'] = indices[item['calculation']]
            charts.append(checked)
            chart_indices.append(index)
        except (ValueError, KeyError, TypeError, DecimalException) as error:
            errors.append({'index':f'figures[{index}]', 'error':str(error)})
    return {'calculations':calculations, 'calculation_indices':indices,
            'charts':charts, 'chart_indices':chart_indices, 'errors':errors}


def _claim_id(batch_id, role_id, text):
    return 'claim_' + fingerprint([batch_id, role_id, text])[:32]


def _claim_text(value):
    if isinstance(value, list) and all(isinstance(item, str) for item in value):
        return '；'.join(value)
    return value


def _claims(session, batch_id, role_id, artifacts, aliases):
    claims, problems = [], []
    for artifact in artifacts:
        content = artifact['content']
        rows = content.get('claims', []) if artifact['kind'] == 'role_claims' else []
        for row in rows:
            if not isinstance(row, dict) or not isinstance(row.get('text'), str) or not row['text'].strip():
                problems.append('分析角色存在缺少正文的claim；原文保留，未加入作者可选判断')
                continue
            refs = [aliases.get(ref, ref) for ref in row.get('evidence_refs', [])]
            missing = [ref for ref in refs if ref not in session.artifacts()
                       and not (ref.startswith('source:') and ref[7:] in session.sources)]
            claim = {key: _claim_text(row[key]) for key in CLAIM_FIELDS if key in row}
            claim.update(id=_claim_id(batch_id, role_id, claim['text']), evidence_refs=refs,
                         producer_role=role_id, artifact_ref=artifact['ref'])
            if missing or not refs:
                claim['unresolved'] = (claim.get('unresolved', '') + '；引用未绑定实际角色材料').strip('；')
                problems.append('claim「' + claim['text'] + '」的材料引用未绑定；保留原句，供作者安排复核')
            claims.append(claim)
    return claims, problems


def record_role_result(session, batch_id, result):
    """Register one actual result before dependent roles start."""
    role_id = result['role_id']
    namespace = ROLE_NAMESPACES.get(role_id)
    if namespace is None:
        return
    record = session.record
    batches = record.setdefault('role_batches', {})
    batch = batches.setdefault(batch_id, {'roles': {}, 'artifact_refs': []})
    if role_id in batch['roles']:
        return
    previous = deepcopy(record['recipe'])
    aliases = _aliases(record, batch_id)
    artifacts = record.setdefault('artifacts', {})
    rows = []
    excerpt_errors = []
    for index, artifact in enumerate(result.get('artifacts', [])):
        ref = f'role:{batch_id}:{role_id}:{index}'
        content = deepcopy(artifact['content'])
        row = {'kind': 'role_' + artifact['kind'], 'content': content,
               'producer_role': role_id, 'sha256': artifact['sha256'],
               'evidence_refs': artifact.get('evidence_refs', []), 'result_ref': result.get('result_ref')}
        artifacts[ref] = row
        rows.append({'ref': ref, **row})
        if artifact['kind'] != 'source_excerpts':
            continue
        for excerpt_index, value in enumerate(content.get('excerpts', [])):
            try:
                excerpt = _excerpt(value, session)
            except ValueError as error:
                detail = {'index': excerpt_index, 'error': str(error)}
                if isinstance(value, dict):
                    detail.update({key: value[key] for key in ('id', 'source_id')
                                   if isinstance(value.get(key), str)})
                row.setdefault('validation_errors', []).append(detail)
                excerpt_errors.append({'artifact_ref': ref, **detail})
                continue
            excerpt_ref = f'{ref}:{excerpt_index}'
            artifacts[excerpt_ref] = {'kind': 'source_excerpt', 'excerpt': excerpt,
                'producer_role': role_id, 'sha256': fingerprint(excerpt), 'result_ref': result.get('result_ref')}
            if isinstance(value.get('id'), str):
                aliases[value['id']] = excerpt_ref
    if role_id == 'finance_calculation' and result['status'] == 'COMPLETE':
        for row in rows:
            try:
                checked = _validate_calculations(session, row['content'], aliases)
            except (ValueError, KeyError, TypeError, DecimalException) as error:
                row['validation_error'] = str(error)
                artifacts[row['ref']]['validation_error'] = str(error)
                continue
            if checked['errors']:
                row['validation_errors'] = checked['errors']
                row['validation_error'] = '；'.join(f"calculations[{error['index']}] {error['error']}"
                                                   for error in checked['errors'])
                artifacts[row['ref']].update(validation_errors=checked['errors'],
                                              validation_error=row['validation_error'])
            calculation_refs = {index: row['ref'] + ':validated:' + str(index)
                                for index in checked['calculation_indices']}
            for index, calculation in zip(checked['calculation_indices'], checked['calculations']):
                ref = row['ref'] + ':validated:' + str(index)
                artifacts[ref] = {'kind': 'validated_calculation', 'calculation': calculation,
                    'producer_role': role_id, 'sha256': fingerprint(calculation),
                    'dependency_refs': {item['source'].split(':')[1]: calculation_refs[int(item['source'].split(':')[1])]
                                        for item in calculation['inputs'] if item['source'].startswith('calculation:')},
                    'semantic_certification': False}
                identifier = calculation.get('id')
                if isinstance(identifier, str):
                    aliases[identifier] = ref
            for index, figure in zip(checked['chart_indices'], checked['charts']):
                ref = row['ref'] + ':figure:' + str(index)
                artifacts[ref] = {'kind': 'figure', 'figure': figure,
                    'producer_role': role_id, 'sha256': fingerprint(figure),
                    'calculation_refs': {str(item['calculation']): calculation_refs[item['calculation']]
                                         for item in figure['items'] if 'calculation' in item}}
                if index < len(row['content'].get('figures', [])):
                    identifier = row['content']['figures'][index].get('id')
                    if isinstance(identifier, str):
                        aliases[identifier] = ref
    claims, problems = _claims(session, batch_id, role_id, rows, aliases)
    stage = {'id': f'stage_role_{batch_id}_{role_id}', 'goal': '角色成果：' + namespace,
             'batch_id':batch_id,
             'owner': 'subagent', 'producer_role': role_id, 'role_status': result['status'],
             'model_status': 'done' if result['status'] == 'COMPLETE' else 'partial',
             'output_refs': [ref for ref, artifact in artifacts.items()
                             if ref.startswith(f'role:{batch_id}:{role_id}:')],
             'claims': claims, 'open_questions': [
                 f"摘录{error.get('id', error['index'])}：{error['error']}"
                 for error in excerpt_errors
             ] + problems + [
                 (issue.get('reason') or issue.get('claim') or str(issue))
                 if isinstance(issue, dict) else str(issue) for issue in result.get('issues', [])
             ] + (['执行错误：' + result['error']] if result.get('error') else [])}
    record['recipe'].setdefault('stages', []).append(stage)
    refs = [ref for ref in artifacts if ref.startswith(f'role:{batch_id}:{role_id}:')]
    batch['roles'][role_id] = {'status': result['status'], 'namespace': namespace,
                               'artifact_refs': refs, 'result_ref': result.get('result_ref')}
    if excerpt_errors:
        batch['roles'][role_id]['validation_errors'] = excerpt_errors
    batch['artifact_refs'].extend(refs)
    _save_recipe(record, previous)


def role_materials(session, batch_id):
    aliases = _aliases(session.record, batch_id)
    return {'aliases': deepcopy(aliases), 'artifacts': {
        ref: artifact for ref, artifact in session.artifacts().items()
        if ref.startswith('role:' + batch_id + ':')}}


def role_batch_summary(session, batch_id):
    batch = session.record.get('role_batches', {}).get(batch_id)
    if batch is None:
        return None
    return {'research_id': session.record['id'], **deepcopy(batch),
            'aliases':deepcopy(session.record.get('role_aliases', {}).get(batch_id, {})),
            'claims':[deepcopy(claim) for stage in session.record['recipe']['stages']
                      if stage.get('batch_id') == batch_id and stage.get('producer_role') == 'finance_analysis'
                      for claim in stage.get('claims', [])],
            'decision_claim_ids': session.record.get('decision_claim_ids', [])}


def _recipe_change(record, update):
    from copy import deepcopy
    previous = deepcopy(record['recipe'])
    record['recipe'].update(update)
    _save_recipe(record, previous)


def adopt_author_materials(session, params):
    """The author selects immutable role artifacts; publication prose remains theirs."""
    if session.record.get('research_contract_version') != 3:
        return params
    from harness.contracts.subagents import TERMINAL_SUBAGENT_STATUSES
    ready = any(set(ROLE_NAMESPACES) <= set(batch['roles'])
                and all(row['status'] in TERMINAL_SUBAGENT_STATUSES
                        for row in batch['roles'].values())
                for batch in session.record.get('role_batches', {}).values())
    if not ready:
        raise ValueError('请先创建四个金融角色并等待整批终态；作者不能跳过角色直接提交事实或计算')
    params = deepcopy(params)
    artifacts = session.artifacts()
    calculation_refs = list(params.pop('calculation_refs', []))
    for row in params.pop('calculations', []):
        calculation_refs.append(row.get('artifact_ref'))
    calculations, selected_calculations, visiting = [], {}, set()

    def adopt_calculation(ref):
        if ref in selected_calculations:
            return selected_calculations[ref]
        if ref in visiting:
            raise ValueError('已校验计算的依赖存在循环：' + str(ref))
        artifact = artifacts.get(ref, {})
        if artifact.get('kind') != 'validated_calculation':
            raise ValueError('v3计算须选择计算角色已校验的calculation_refs：' + str(ref))
        visiting.add(ref)
        calculation = deepcopy(artifact['calculation'])
        for item in calculation['inputs']:
            if item['source'].startswith('calculation:'):
                dependency = artifact.get('dependency_refs', {}).get(item['source'].split(':')[1])
                if not dependency:
                    raise ValueError('已校验计算缺少实际依赖引用：' + str(ref))
                item['source'] = 'calculation:' + str(adopt_calculation(dependency))
        selected_calculations[ref] = len(calculations)
        calculations.append(calculation)
        visiting.remove(ref)
        return selected_calculations[ref]

    for ref in calculation_refs:
        adopt_calculation(ref)
    params['calculations'] = calculations
    chart_refs = list(params.pop('chart_refs', []))
    for row in params.pop('charts', []):
        chart_refs.append(row.get('artifact_ref'))
    charts = []
    for ref in chart_refs:
        artifact = artifacts.get(ref, {})
        if artifact.get('kind') != 'figure':
            raise ValueError('v3图表须选择计算角色已校验的chart_refs：' + str(ref))
        chart = deepcopy(artifact['figure'])
        for item in chart['items']:
            if 'calculation' in item:
                dependency = artifact.get('calculation_refs', {}).get(str(item['calculation']))
                if not dependency:
                    raise ValueError('已校验图表缺少实际计算引用：' + str(ref))
                item['calculation'] = adopt_calculation(dependency)
        charts.append(chart)
    params['charts'] = charts
    sources = []
    for row in params.get('sources', []):
        artifact = artifacts.get(row.get('artifact_ref'), {})
        if artifact.get('kind') != 'source_excerpt':
            raise ValueError('v3引文须选择取证角色的artifact_ref')
        excerpt = artifact['excerpt']
        source = {'id': excerpt['source_id'], 'quote': excerpt['quote'], 'locator': excerpt['locator']}
        if all(key in excerpt for key in ('page', 'start', 'end')):
            source['excerpt'] = {key: excerpt[key] for key in ('page', 'start', 'end')}
        sources.append(source)
    params['sources'] = sources
    decisions = params.pop('decision_claim_ids', [])
    claims = {claim['id']: (stage, claim) for stage in session.record['recipe']['stages']
              if stage.get('owner') == 'subagent' for claim in stage.get('claims', [])}
    if not isinstance(decisions, list) or not decisions or any(ref not in claims for ref in decisions):
        raise ValueError('decision_claim_ids须选择分析角色实际返回的claims，供标题、首段和核心结论使用')
    selected = []
    for ref in decisions:
        stage, claim = claims[ref]
        kinds = {artifacts.get(key, {}).get('kind') for key in claim['evidence_refs']}
        kinds.update('source' for key in claim['evidence_refs'] if key.startswith('source:'))
        if claim.get('kind', 'inference') == 'calculation':
            valid = 'validated_calculation' in kinds
        else:
            valid = bool(kinds & {'source', 'source_excerpt', 'validated_calculation'})
        if not valid or claim.get('producer_role') != 'finance_analysis':
            raise ValueError('核心claim须由分析角色提出，并绑定真实摘录或已校验计算：' + ref)
        selected.append({'claim_id': ref, 'stage_id': stage['id'], 'claim': deepcopy(claim)})
    _recipe_change(session.record, {'decision_claim_ids': decisions})
    session.record['decision_claim_ids'] = decisions
    session.record['decision_claims'] = selected
    return params


def review_context_fingerprint(session, candidate, question):
    if session.record.get('research_contract_version') != 3:
        return fingerprint(session.evidence_index())
    artifacts = {ref: {key: artifact.get(key) for key in ('kind', 'sha256', 'producer_role')}
                 for ref, artifact in session.artifacts().items() if ref.startswith('role:')}
    from .material_view import cited_page_context, publication_links
    return fingerprint({'contract': 3, 'question': question, 'candidate': candidate,
                        'methods': session.method_text, 'role_artifacts': artifacts,
                        'cited_page_context': cited_page_context(session, candidate),
                        'source_links': publication_links(candidate, session.sources),
                        'publication_labels': {key:source['publication_labels'] for key,source in
                            session.sources.items() if source.get('publication_labels')},
                        'decision_claim_ids': session.record.get('decision_claim_ids', [])})
