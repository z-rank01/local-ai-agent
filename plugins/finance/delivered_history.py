"""Replace completed research tool exchanges with their recoverable delivery index."""
import json
import re

from harness.contracts.history_projection import HistoryProjection


def _delivery_span(history, start, stop):
    pending = {}
    seen = set()
    begin = directory = None
    group = start
    for i in range(start, stop):
        message = history[i]
        if message.get('role') == 'assistant':
            if pending:
                return None
            group = i
            for call in message.get('tool_calls') or []:
                call_id = call.get('id')
                if not call_id or call_id in seen:
                    return None
                seen.add(call_id)
                pending[call_id] = call.get('function', {}).get('name')
        elif message.get('role') == 'tool':
            name = message.get('tool_name')
            if not name or pending.pop(message.get('tool_call_id'), None) != name:
                return None
            if name not in ('research_begin', 'brief_publish'):
                continue
            try:
                result = json.loads(message['content'])
            except (KeyError, TypeError, ValueError):
                continue
            if not isinstance(result, dict):
                continue
            if name == 'research_begin':
                research_id = result.get('research_id')
                if isinstance(research_id, str) and re.fullmatch('[0-9a-f]{32}', research_id):
                    root = '/workspace/reports/chat-research/' + research_id
                    if result.get('directory') != root or (directory and directory != root):
                        return None
                    if begin is None:
                        begin, directory = group, root
            elif (directory and not pending and result.get('status') == 'PUBLISHED'
                  and result.get('path') == directory + '/report.html'
                  and re.fullmatch('[0-9a-f]{64}', str(result.get('sha256', '')))):
                delivery = {'path':result['path'], 'title':result.get('title', ''),
                            'candidate_sha256':result['sha256']}
                return begin, i + 1, delivery
    return None


def delivered_history(history, archive):
    users = [i for i, message in enumerate(history) if message.get('role') == 'user']
    output, evidence_ids = list(history), set()
    count = 0

    def save(message):
        evidence_id = archive(message)
        if not isinstance(evidence_id, str) or not re.fullmatch('[0-9a-f]{64}', evidence_id):
            raise ValueError('已发布研究历史未完整归档')
        evidence_ids.add(evidence_id)
        return evidence_id

    if archive is not None:
        for left, right in reversed(list(zip(users, users[1:]))):
            span = _delivery_span(history, left + 1, right)
            if not span:
                continue
            begin, end, delivery = span
            index, prose = [], []
            for message in history[begin:end]:
                if message.get('tool_calls') or message.get('role') == 'tool':
                    # Store exactly the authorized visible envelope. The
                    # evidence reader also returns the archived call arguments.
                    public = {k:message[k] for k in ('role','content','tool_calls','tool_name','tool_call_id')
                              if k in message}
                    entry = {'role':public['role'], 'evidence_id':save(public)}
                    if public.get('tool_calls'):
                        entry['tools'] = [call['function']['name'] for call in public['tool_calls']]
                    else:
                        entry['tool'] = public.get('tool_name')
                    index.append(entry)
                    if public['role'] == 'assistant' and public.get('content'):
                        prose.append({'role':'assistant', 'content':public['content']})
                else:
                    prose.append(message)
            delivery['history_index'] = save({'role':'assistant', 'record_kind':'published_research_index',
                'content':json.dumps(index, ensure_ascii=False)})
            delivery['reading'] = ('这是已正式发布的历史版本。追问先读这份报告及其引用来源；'
                '需要还原取证或计算过程时，可用context_evidence_read读取索引及对应回执。')
            output[begin:end] = [*prose, {'role':'assistant','content':json.dumps(delivery, ensure_ascii=False)}]
            count += 1
    return HistoryProjection(output, evidence_ids,
        {'view_version':'published-research-history-v1'} if count else {},
        {'published_turns':count, 'archived_evidence':len(evidence_ids)})
