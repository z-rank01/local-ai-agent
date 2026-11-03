"""Parent-run checkpoint and evidence adapter for subagent batches."""
from dataclasses import asdict
from hashlib import sha256
import json


def preserve_subagent_batches(history, checkpoint):
    if 'subagent_batches' in history:
        checkpoint['subagent_batches'] = history['subagent_batches']
    return checkpoint


class RunSubagentStore:
    def __init__(self, control, context):
        if control is None:
            raise ValueError('子Agent需要父运行控制上下文')
        self.control = control
        self.context = context

    def _entry(self, batch_id, *, create=False):
        checkpoint = self.control.manager.store.get(self.control.id)['checkpoint']
        batches = checkpoint.setdefault('subagent_batches', {})
        if batch_id not in batches:
            if not create:
                raise ValueError('子Agent批次不存在：' + batch_id)
            batches[batch_id] = {'role_ids':[], 'results':{}}
        return checkpoint, batches[batch_id]

    def _archive(self, value):
        value = dict(value)
        kind = value.pop('record_kind')
        evidence_id = self.context.archive({'role':'assistant', 'record_kind':kind,
            'content':json.dumps(value, ensure_ascii=False)})
        self.context.evidence_ids.add(evidence_id)
        return evidence_id

    def create_batch(self, batch_id, tasks):
        checkpoint, batch = self._entry(batch_id, create=True)
        batch['role_ids'] = [task.role.role_id for task in tasks]
        batch['spec_ref'] = self._archive({'record_kind':'subagent_batch',
            'batch_id':batch_id, 'tasks':[{'role':task.role.role_id, 'task':task.text,
                'plugin':task.role.plugin_id, 'plugin_version':task.role.plugin_version,
                'tools':sorted(task.role.tools), 'depends_on':list(task.role.depends_on),
                'prompt_sha256':sha256(task.role.prompt.encode()).hexdigest(),
                'output_namespace':task.role.recipe_namespace,
                'evidence_ids':list(task.evidence_ids)} for task in tasks]})
        self.control.manager.store.update(self.control.id, checkpoint=checkpoint)
        self.control.manager.publish(self.control.id, {'event':'subagent.batch',
            'data':{'batch_id':batch_id, 'role_ids':batch['role_ids'], 'status':'RUNNING'}})

    def save_result(self, batch_id, result):
        checkpoint, batch = self._entry(batch_id)
        result_ref = self._archive({'record_kind':'subagent_result',
            'batch_id':batch_id, 'result':asdict(result)})
        batch['results'][result.role_id] = {'status':result.status, 'result_ref':result_ref}
        self.control.manager.store.update(self.control.id, checkpoint=checkpoint)
        self.control.manager.publish(self.control.id, {'event':'subagent.result',
            'data':{'batch_id':batch_id, 'role_id':result.role_id, 'status':result.status,
                    'result_ref':result_ref}})

    def _read_result(self, result_ref):
        cursor, chunks = 0, []
        while True:
            page = self.context.read_evidence(result_ref, cursor)
            chunks.append(page.get('content', ''))
            cursor = page.get('next_cursor')
            if cursor is None:
                return json.loads(''.join(chunks))['result']

    def get_batch(self, batch_id):
        checkpoint = self.control.manager.store.get(self.control.id)['checkpoint']
        if batch_id not in checkpoint.get('subagent_batches', {}):
            raise ValueError('子Agent批次不存在：' + batch_id)
        _, batch = self._entry(batch_id)
        results = {}
        for role_id, row in batch['results'].items():
            results[role_id] = {**self._read_result(row['result_ref']),
                                'result_ref':row['result_ref']}
        return {'batch_id':batch_id, 'role_ids':list(batch['role_ids']),
                'spec_ref':batch.get('spec_ref'), 'results':results}
