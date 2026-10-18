"""Index large, older research tool bodies without another model call.

Only the transient model view changes. Original protocol stays in the existing
conversation evidence store and can be read through context_evidence_read.
"""
from hashlib import sha256
import json
import logging
import re


class ResearchMaterialView:
    def __init__(self):
        self.archived = {}

    def apply(self, messages, manager):
        archive = getattr(manager, 'archive', None)
        if archive is None or getattr(manager, 'reader', None) is None:
            return messages
        last_user = max((i for i, row in enumerate(messages) if row.get('role') == 'user'), default=-1)
        # Keep the two latest tool batches intact, including their arguments.
        completed = {row.get('tool_call_id') for row in messages[last_user+1:] if row.get('role') == 'tool'}
        batches = [i for i, row in enumerate(messages) if i > last_user and row.get('role') == 'assistant'
                   and any(call.get('id') in completed for call in row.get('tool_calls', []))]
        if len(batches) <= 2:
            return messages
        boundary = batches[-2]
        eligible = {}
        for row in messages[last_user+1:boundary]:
            for call in row.get('tool_calls', []):
                if call.get('id') in completed:
                    eligible[call['id']] = call['function']['name']
        view = []
        for i, row in enumerate(messages):
            if not last_user < i < boundary:
                view.append(row)
                continue
            changed = row
            if row.get('role') == 'tool' and row.get('tool_call_id') in eligible:
                content = row.get('content') or ''
                # Small figures, errors and method notes stay directly readable.
                if len(content) > 2400:
                    eid = self._save(row, manager, archive)
                    if eid:
                        pointer = {'evidence_id': eid, 'tool': eligible[row['tool_call_id']],
                            'original_chars': len(content), 'preview_start': content[:700],
                            'preview_end': content[-400:],
                            'reading': '旧大回执完整保存在本会话证据中；预览不是全文。需要引用、反证或细节时用context_evidence_read回读，不重复抓取同一资料。'}
                        try:
                            value, _ = json.JSONDecoder().raw_decode(content)
                        except ValueError:
                            value = {}
                        if isinstance(value, dict):
                            pointer['location'] = {key: value[key] for key in
                                ('source_id', 'url', 'saved_source', 'path', 'next_offset', 'next_cursor') if key in value}
                        changed = {**row, 'content': json.dumps(pointer, ensure_ascii=False)}
            elif row.get('role') == 'assistant' and row.get('tool_calls'):
                calls = []
                for call in row['tool_calls']:
                    fn = call['function']
                    args = fn.get('arguments', {})
                    if call.get('id') in eligible and len(json.dumps(args, ensure_ascii=False)) > 2400:
                        # Archive only public tool arguments, never reasoning fields.
                        eid = self._save({'role': 'assistant', 'tool_name': fn['name'],
                            'tool_call_id': call['id'], 'content': json.dumps(call, ensure_ascii=False)}, manager, archive)
                        if eid:
                            call = {**call, 'function': {**fn, 'arguments': {'archived_arguments': eid,
                                'reading': '历史实际参数完整归档；这只是视图指针，不是重新执行的参数。'}}}
                    calls.append(call)
                changed = {**row, 'tool_calls': calls}
            view.append(changed)
        return view

    def _save(self, row, manager, archive):
        key = sha256(json.dumps(row, ensure_ascii=False, sort_keys=True).encode()).hexdigest()
        eid = self.archived.get(key)
        if eid is None:
            try:
                eid = archive({**row, 'record_kind': 'research_tool_material'})
            except (OSError, ValueError, RuntimeError):
                logging.getLogger(__name__).exception('Research material archive failed; retaining original')
                return None
            if not isinstance(eid, str) or not re.fullmatch('[0-9a-f]{64}', eid):
                return None
            self.archived[key] = eid
        manager.evidence_ids.add(eid)
        return eid
