"""Read-only views of the recorded parent turn and its request manifests."""
import json
from hashlib import sha256

from harness.runtime.evidence import fingerprint
from plugins.finance.research.session import encoded
from harness.runtime.subagents.materials import role_prompt
from harness.runtime.subagents.spec import load_role_specs


def registered_roles(root):
    plugins = root / 'plugins'
    granted = {tool for manifest in plugins.glob('*/plugin.json')
               for role in json.loads(manifest.read_text(encoding='utf8')).get('agent_roles', [])
               for tool in role.get('tools', [])}
    return load_role_specs(plugins, granted)


def ledger_input(db, ledger, research, workspace, native, role):
    if role.tools or not native.get('ledger_call_id'):
        raise ValueError('Missing a no-tool role ledger call')
    entry = evidence(db, research, workspace, native.get('material_ref'), cloud_only=True)
    if not entry or json.loads(entry[1]).get('record_kind') != 'subagent_input':
        raise ValueError('Missing cloud-authorized role material')
    messages = [{'role':'system', 'content':role_prompt(role)}, {'role':'user', 'content':entry[0]}]
    receipt = ledger.execute('SELECT execution_hash,input_hash FROM research_receipts WHERE call_id=?',
                             (native['ledger_call_id'],)).fetchone()
    digest = sha256(json.dumps(messages, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    if not receipt or tuple(receipt) != (native.get('execution_hash'), digest):
        raise ValueError('Role prompt and material do not match the original call hash')
    return messages


def evidence(db, research, workspace, ref, *, cloud_only=False):
    scopes = ('cloud:' + workspace, ('cloud:' if cloud_only else 'local:') + workspace)
    return db.execute('SELECT content,metadata FROM task_evidence WHERE id=? AND conversation_id=? '
                      'AND scope IN (?,?)', (ref, research['conversation_id'], *scopes)).fetchone()


def request_view(db, research, workspace, index, show):
    entry = evidence(db, research, workspace, index.get('manifest_ref'))
    result = {k: index.get(k) for k in ('manifest_ref', 'call_id', 'local_attempt_id',
                                        'visible_input_sha256', 'recipe_revision')}
    result['input'] = 'UNCONFIRMED'
    if not entry or json.loads(entry[1]).get('record_kind') != 'research_request_manifest':
        return result
    manifest = json.loads(entry[0])
    if any(manifest.get(k) != index.get(k) for k in
           ('visible_input_sha256', 'local_attempt_id', 'call_id', 'messages')):
        return result
    result['input'] = 'SERIALIZED_MANIFEST'
    result['messages'] = [m.get('evidence_id') for m in manifest['messages']]
    materials = manifest['materials']
    result['located_materials'] = sum(bool(m['included']) for m in materials)
    result['total_materials'] = len(materials)
    if show:
        result['materials'] = materials
    return result


def confirmed_messages(db, research, workspace, index):
    if request_view(db, research, workspace, index, False)['input'] != 'SERIALIZED_MANIFEST':
        raise ValueError('Missing matching request manifest')
    messages = []
    for i, meta in enumerate(index['messages']):
        entry = evidence(db, research, workspace, meta['evidence_id'], cloud_only=True)
        if not entry:
            raise ValueError('Missing cloud-authorized archived message')
        data = json.loads(entry[1])
        msg = {k: v for k, v in data.items() if k in ('role', 'tool_calls', 'tool_call_id')}
        msg['content'] = entry[0]
        if i != meta['index'] or fingerprint(msg) != meta['sha256']:
            raise ValueError('Archived message does not match serialized message')
        messages.append(msg)
    if fingerprint(messages) != index['visible_input_sha256']:
        raise ValueError('Request fingerprint mismatch')
    return messages


def review_rows(research, path=None):
    decisions = research.get('publication_decisions') or []
    previous = None
    seen = set()
    rows = []
    results = research.get('reviews', {}).values()
    if path:
        attempts = sorted((f for f in (path/'reviews').glob('*-result.json')
                           if f.name.split('-')[0].isdigit()), key=lambda f:int(f.name.split('-')[0]))
        if attempts:
            results = [json.loads(f.read_text('utf8')) for f in attempts]
            ids = {(v.get('native_receipt') or {}).get('local_attempt_id') for v in results}
            results.extend(v for v in research.get('reviews', {}).values()
                           if not (v.get('native_receipt') or {}).get('local_attempt_id') in ids)
    for value in results:
        candidate = value.get('candidate_sha256')
        receipt = value.get('native_receipt') or {}
        rows.append({'status': value['status'], 'ref': value.get('review_ref'), 'candidate': candidate,
                     'candidate_change': ('FIRST' if previous is None else
                                          'SAME' if previous == candidate else 'CHANGED'),
                     'candidate_seen_before': candidate in seen,
                     'attempt_refs': value.get('attempts', []), 'native_attempt': receipt.get('local_attempt_id'),
                     'usage': receipt.get('usage'), 'failure_stage': receipt.get('failure_stage'),
                     'terminal_received': receipt.get('terminal_received'), 'issues': value.get('issues'),
                     'matching_author_decision': any(value.get('review_ref') and
                         all(value.get(k) == d.get(k) for k in ('review_ref','candidate_sha256','evidence_sha256'))
                         and d.get('review_response') for d in decisions)})
        previous = candidate
        seen.add(candidate)
    return rows


def publication(research, path):
    report = path / 'report.html'
    return {'recorded': research['status'], 'report_html': report.is_file(),
            'report_sha256': sha256(report.read_bytes()).hexdigest() if report.is_file() else None,
            'decisions': research.get('publication_decisions') or []}


def source_positions(research, workspace_root):
    checks = []
    for source_id, excerpts in research.get('excerpts', {}).items():
        source = research.get('sources', {}).get(source_id, {})
        relative = source.get('path', '').removeprefix('/workspace/')
        path = (workspace_root / relative).resolve()
        if not path.is_relative_to(workspace_root.resolve()) or not path.is_file():
            checks.append({'source_id': source_id, 'verification': 'ORIGINAL_UNAVAILABLE'})
            continue
        document = json.loads(path.read_text('utf8'))
        if sha256(encoded(document).encode()).hexdigest() != source.get('artifact_sha256'):
            checks.append({'source_id': source_id, 'verification': 'ORIGINAL_HASH_MISMATCH'})
            continue
        pages = {p['page']:p['text'] for p in document['pages']}
        for excerpt in excerpts:
            page = pages.get(excerpt.get('page'))
            start, end = excerpt.get('start'), excerpt.get('end')
            valid = (source.get('sha256') == excerpt.get('sha256') and page is not None and
                     type(start) is int and type(end) is int and 0 <= start < end <= len(page)
                     and page[start:end] == excerpt.get('quote'))
            checks.append({'source_id': source_id, 'page':excerpt.get('page'), 'start':start, 'end':end,
                           'verification':'EXACT' if valid else 'MISMATCH'})
    return checks


def followups(db, research, workspace):
    path = research['directory'] + '/report.html'
    published = db.execute("SELECT updated_at FROM chat_runs WHERE workspace_id=? AND conversation_id=? "
                           "AND json_extract(checkpoint,'$.published.path')=? ORDER BY updated_at LIMIT 1",
                           (workspace, research['conversation_id'], path)).fetchone()
    if not published:
        return []
    indices = {ref: len(json.loads(content)) for ref, content in db.execute(
        "SELECT id,content FROM task_evidence WHERE conversation_id=? AND scope IN (?,?) "
        "AND json_extract(metadata,'$.record_kind')='published_research_index'",
        (research['conversation_id'], 'cloud:'+workspace, 'local:'+workspace))}
    rows = db.execute('SELECT id,status,checkpoint FROM chat_runs WHERE conversation_id=? AND workspace_id=? '
                      'AND created_at>? ORDER BY created_at', (research['conversation_id'], workspace, published[0]))
    return [{'run':run, 'status':status, 'checkpoint_history_indices':
             {ref:indices[ref] for ref in json.loads(checkpoint).get('evidence_ids', []) if ref in indices}}
            for run,status,checkpoint in rows]
