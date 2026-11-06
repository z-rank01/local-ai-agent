"""One explicit developer experiment against a confirmed public review input."""
import argparse
import asyncio
from contextlib import closing
import json
from pathlib import Path
import re
import sqlite3
from types import SimpleNamespace
from uuid import uuid4

from core import config
from core.audit_logger import AuditLogger
from core.conversation_store import ConversationStore
from core.model_execution import invoke
from core.providers import ModelRegistry
from harness.runtime.evidence import fingerprint
from plugins.finance.research_review import parse_review
from tests.finance_recipe_stages import confirmed_messages


class ReadOnlyStore(ConversationStore):
    def __init__(self, path):
        self.uri = path.as_uri() + '?mode=ro'

    def _connect(self):
        db = sqlite3.connect(self.uri, uri=True)
        db.row_factory = sqlite3.Row
        return db


async def probe(runtime, profile, messages, change, *, call=invoke):
    if [m['role'] for m in messages] != ['system', 'user']:
        raise ValueError('This transport supports only the archived two-message no-tool decision')
    result = await call(runtime, profile, messages[0]['content'] + '\n' + change, messages[1]['content'],
                        request_id='recipe-probe-' + uuid4().hex, structured_output=True)
    output = {k: result.get(k) for k in ('status','usage','usage_status','ledger_call_id','finish_reason','error')}
    if result['status'] == 'COMPLETE':
        try:
            output['review'] = parse_review(result['text'])
        except ValueError as exc:
            output['contract_error'] = str(exc)
    return output


async def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('workspace','research','run','attempt'):
        parser.add_argument(name)
    parser.add_argument('--stage', required=True, choices=['review'])
    parser.add_argument('--change', required=True)
    parser.add_argument('--live', action='store_true')
    args = parser.parse_args()
    if not re.fullmatch('[0-9a-f]{12}', args.workspace) or not re.fullmatch('[0-9a-f]{32}', args.research):
        parser.error('Invalid workspace or research ID')
    if not args.change.strip():
        parser.error('Describe the actual system prompt change')
    root = Path(__file__).resolve().parents[1]
    path = root / 'data/workspace/.harness' / args.workspace / 'reports/chat-research' / args.research
    research = json.loads((path / 'research.json').read_text('utf8'))
    store = ReadOnlyStore(root / 'data/conversations.db')
    with closing(store._connect()) as db:
        row = db.execute('SELECT checkpoint FROM chat_runs WHERE id=? AND workspace_id=? AND conversation_id=?',
                         (args.run,args.workspace,research['conversation_id'])).fetchone()
        if not row:
            parser.error('Run does not belong to selected research conversation')
        checkpoint = json.loads(row[0])
        if (checkpoint.get('published') or {}).get('path') != research['directory'] + '/report.html':
            parser.error('Run lacks exact publication-to-research binding')
        frozen = checkpoint['frozen']
        if frozen['model']['id'] != 'deepseek:deepseek-flash' or not frozen['cloud_read']:
            parser.error('Missing frozen DeepSeek profile or cloud authorization')
        indices = [x for x in research['request_materials'] if x.get('local_attempt_id') == args.attempt]
        reviews = [x for x in research['reviews'].values()
                   if (x.get('native_receipt') or {}).get('local_attempt_id') == args.attempt]
        if len(indices) != 1 or len(reviews) != 1 or reviews[0]['status'] != 'COMPLETE':
            parser.error('Select one confirmed historical review')
        messages = confirmed_messages(db, research, args.workspace, indices[0])
    model = frozen['model']
    profile = dict(version=1, conversation_id=research['conversation_id'], workspace_id=args.workspace,
                   run_id=args.run, cloud_read=True, model={**model, 'kind':'cloud'})
    print(json.dumps({'original_input': fingerprint(messages), 'prompt_change':args.change,
                      'baseline_issues':len(reviews[0].get('issues', [])), 'live':args.live}, ensure_ascii=False))
    if args.live:
        models = ModelRegistry()
        try:
            runtime = SimpleNamespace(models=models, store=store, audit=AuditLogger(config.LOG_PATH))
            result = await probe(runtime, profile, messages, args.change)
            print(json.dumps(result, ensure_ascii=False))
        finally:
            await models.close()


if __name__ == '__main__':
    asyncio.run(main())
