"""Developer-only offline replay. No runtime registration, network or write path."""
import argparse
import json
from pathlib import Path
import re
import sqlite3
from contextlib import closing

from harness.contracts.subagents import RoleTask
from harness.runtime.subagents.results import structured_result
from harness.runtime.subagents.spec import load_role_specs


def inspect_role(db, research, workspace, batch_id, role, saved, show):
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
    parser.add_argument('--show-artifacts', action='store_true')
    args = parser.parse_args()
    if not re.fullmatch('[0-9a-f]{12}', args.workspace) or not re.fullmatch('[0-9a-f]{32}', args.research):
        parser.error('expected named workspace and research IDs')
    root = Path(__file__).resolve().parents[1]
    path = root / 'data/workspace/.harness' / args.workspace / 'reports/chat-research' / args.research
    research = json.loads((path / 'research.json').read_text(encoding='utf-8'))
    if research.get('id') != args.research or research.get('research_contract_version') != 3:
        parser.error('expected matching v3 research')
    plugins = root / 'plugins'
    tools = {name for manifest in plugins.glob('*/plugin.json')
             for role in json.loads(manifest.read_text(encoding='utf-8')).get('agent_roles', [])
             for name in role.get('tools', [])}
    roles = load_role_specs(plugins, tools)
    with closing(sqlite3.connect((root / 'data/conversations.db').as_uri() + '?mode=ro', uri=True)) as db:
        rows = [inspect_role(db, research, args.workspace, batch, roles[name], result, args.show_artifacts)
                for batch, data in research['role_batches'].items() if not args.batch or args.batch == batch
                for name, result in data['roles'].items() if not args.role or name == args.role]
    if not rows:
        parser.error('no matching archived batch/role')
    decisions = research.get('publication_decisions') or []
    reviews = [{'status': value['status'], 'ref': value.get('review_ref'),
                'candidate': value.get('candidate_sha256'),
                'matching_author_decision': any(value.get('review_ref') and
                    all(value.get(k) == decision.get(k) for k in
                        ('review_ref', 'candidate_sha256', 'evidence_sha256'))
                    and decision.get('review_response') for decision in decisions)}
               for value in research.get('reviews', {}).values()]
    print(json.dumps({'roles': rows, 'reviews': reviews, 'report_html': (path / 'report.html').is_file()},
                     ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
