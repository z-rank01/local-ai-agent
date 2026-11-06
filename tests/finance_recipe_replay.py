"""Developer-only offline replay. No runtime registration, network or write path."""
import argparse
import json
from pathlib import Path
import re
import sqlite3
from contextlib import closing

from harness.contracts.subagents import RoleTask
from harness.runtime.subagents.results import structured_result
from tests.finance_recipe_stages import (evidence, followups, ledger_input, publication, registered_roles,
                                        request_view, review_rows, source_positions)


def inspect_role(db, research, workspace, batch_id, role, saved, show, ledger=None):
    row = {'batch': batch_id, 'role': role.role_id, 'recorded': saved['status'],
           'result_ref': saved.get('result_ref')}
    scopes = ('cloud:' + workspace, 'local:' + workspace)
    entry = db.execute('SELECT content FROM task_evidence WHERE id=? AND conversation_id=? '
                       'AND scope IN (?,?)', (row['result_ref'], research['conversation_id'], *scopes)).fetchone()
    if not entry:
        return dict(row, replay='ARCHIVE_UNAVAILABLE')
    envelope = json.loads(entry[0])
    result = envelope['result']
    if envelope.get('batch_id') != batch_id or result.get('role_id') != role.role_id:
        return dict(row, replay='IDENTITY_MISMATCH')
    raw = result.get('raw_text', '')
    row.update(visible_chars=len(raw), recorded_error=result.get('error', ''))
    row['blocked_by'] = (row['recorded_error'].removeprefix('依赖角色未完成：')
                         if row['recorded_error'].startswith('依赖角色未完成：') else None)
    native = result.get('native_receipt') or {}
    calls = native.get('calls') or []
    row['calls'] = [{k:c.get(k) for k in ('call_id','local_attempt_id','result_status','usage')}
                    for c in calls]
    material_ref = native.get('material_ref')
    if material_ref:
        material = evidence(db, research, workspace, material_ref)
        if material and json.loads(material[1]).get('record_kind') == 'subagent_input':
            body = json.loads(material[0])
            row['prepared_input'] = {'material_ref':material_ref, 'task':body.get('task'),
                                     'prior_roles':list(body.get('prior_results') or {})}
    row['serialized_requests'] = [request_view(db, research, workspace, index, show)
                                  for index in research.get('request_materials', [])
                                  if index.get('local_attempt_id') and any(
                                      index['local_attempt_id'] == c.get('local_attempt_id') for c in calls)]
    row['downstream_input'] = 'UNCONFIRMED'
    if native.get('ledger_call_id'):
        row['calls'].append({k:native.get(k) for k in ('ledger_call_id','status','usage')})
        if ledger is not None:
            try:
                ledger_input(db, ledger, research, workspace, native, role)
                row['role_input'] = 'LEDGER_HASH_MATCH'
            except ValueError as exc:
                row['role_input'] = str(exc)
    if result['status'] in ('UNKNOWN', 'STOPPED', 'TRUNCATED') or not raw:
        return dict(row, replay='NO_CONFIRMED_OUTPUT')
    try:
        parsed = structured_result(RoleTask(role, 'offline replay'), raw, status='COMPLETE')
    except ValueError as exc:
        return dict(row, replay='CONTRACT_ERROR', detail=str(exc))
    archived = [item['sha256'] for item in result['artifacts']]
    row.update(replay='PARSED', matches_archived=[item.sha256 for item in parsed.artifacts] == archived)
    mapped = research['artifacts']
    row['mapping'] = [{'ref': ref, 'present': ref in mapped,
                       'errors': mapped.get(ref, {}).get('validation_errors', []),
                       'error': mapped.get(ref, {}).get('validation_error')}
                      for ref in saved['artifact_refs']]
    if show:
        row['public_artifacts'] = [item.content for item in parsed.artifacts]
    return row


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('workspace')
    parser.add_argument('research')
    parser.add_argument('--role')
    parser.add_argument('--batch')
    parser.add_argument('--stage', choices=('all', 'roles', 'author', 'review', 'publication', 'followup',
                                            'evidence', 'calculation', 'analysis', 'role_review'), default='all')
    parser.add_argument('--show-artifacts', action='store_true')
    args = parser.parse_args()
    if not re.fullmatch('[0-9a-f]{12}', args.workspace) or not re.fullmatch('[0-9a-f]{32}', args.research):
        parser.error('expected named workspace and research IDs')
    root = Path(__file__).resolve().parents[1]
    path = root / 'data/workspace/.harness' / args.workspace / 'reports/chat-research' / args.research
    research = json.loads((path / 'research.json').read_text(encoding='utf-8'))
    if research.get('id') != args.research or research.get('research_contract_version') != 3:
        parser.error('expected matching v3 research')
    roles = registered_roles(root)
    selected_role = {'evidence':'finance_evidence', 'calculation':'finance_calculation',
                     'analysis':'finance_analysis', 'role_review':'finance_review'}.get(args.stage, args.role)
    if args.role and selected_role != args.role:
        parser.error('conflicting stage and role')
    out = {}
    with closing(sqlite3.connect((root / 'data/conversations.db').as_uri() + '?mode=ro', uri=True)) as db, \
         closing(sqlite3.connect((root / 'data/model-calls.sqlite').as_uri() + '?mode=ro', uri=True)) as ledger:
        if args.stage in ('all', 'roles', 'evidence', 'calculation', 'analysis', 'role_review'):
            out['roles'] = [inspect_role(db, research, args.workspace, batch, roles[name], result, args.show_artifacts, ledger)
                            for batch, data in research['role_batches'].items() if not args.batch or args.batch == batch
                            for name, result in data['roles'].items() if not selected_role or name == selected_role]
            if not out['roles']:
                parser.error('no matching archived batch/role')
            out['batch_diagnosis'] = [{
                'batch':batch,
                'contract_errors':[r['role'] for r in out['roles'] if r['batch']==batch and r['replay']=='CONTRACT_ERROR'],
                'dependency_failures':{r['role']:r['blocked_by'] for r in out['roles']
                                       if r['batch']==batch and r.get('blocked_by')},
                'unresolved':[r['role'] for r in out['roles'] if r['batch']==batch and
                              r['replay'] not in ('CONTRACT_ERROR','PARSED') and not r.get('blocked_by')]
            } for batch in dict.fromkeys(r['batch'] for r in out['roles'])]
        if args.stage in ('all', 'author'):
            out['author'] = {'question': research.get('review_question'),
                             'selected_claims': research.get('decision_claim_ids'),
                             'tools': [{k: v for k, v in event.items() if k in ('tool','status','at','phase')}
                                       for event in research.get('trace', [])],
                             'requests': [request_view(db, research, args.workspace, item, args.show_artifacts)
                                          for item in research.get('request_materials', [])]}
        if args.stage in ('all', 'followup'):
            out['followup'] = followups(db, research, args.workspace)
        if args.stage in ('all', 'evidence'):
            out['source_positions'] = source_positions(research, root / 'data/workspace/.harness' / args.workspace)
        if args.stage in ('all', 'review'):
            out['reviews'] = review_rows(research, path)
        if args.stage in ('all', 'publication'):
            out['publication'] = publication(research, path)
            out['report_html'] = out['publication']['report_html']
    print(json.dumps(out, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
