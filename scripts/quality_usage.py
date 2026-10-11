"""Read-only fixed-evidence batch usage audit, including pre-restart attempts."""
from collections import Counter
import json


def summarize_calls(rows, batch):
    calls, seen = [], set()
    for row in rows:
        body = row['body'] if isinstance(row['body'], dict) else json.loads(row['body'])
        if body.get('batch') != batch:
            continue
        if row['id'] in seen:
            raise ValueError('Duplicate ledger call ID')
        seen.add(row['id'])
        reply = body.get('reply', {})
        usage = reply.get('usage')
        valid_usage = (isinstance(usage, dict) and
                       all(type(usage.get(k)) is int and usage[k] >= 0
                           for k in ('prompt_tokens', 'completion_tokens', 'total_tokens')))
        elapsed = body.get('elapsed_seconds')
        calls.append({'call_id': row['id'], 'sample': body.get('sample'),
                      'request_id': body.get('request_id'), 'status': row['status'],
                      'usage': usage if valid_usage else None,
                      'elapsed_seconds': elapsed if type(elapsed) in (int, float) and elapsed >= 0 else None,
                      'response_id': reply.get('response_id'),
                      'finish_reason': reply.get('finish_reason'),
                      'profile': body.get('profile'), 'execution_hash': body.get('execution_hash')})
    statuses = Counter(c['status'] for c in calls)
    known = [c for c in calls if c['usage'] is not None]
    elapsed = [c['elapsed_seconds'] for c in calls if c['elapsed_seconds'] is not None]
    count = len(calls)
    return {'batch': batch, 'attempt_count': count, 'statuses': dict(statuses),
            'known_failure_fraction': statuses['FAILED'] / count if count else None,
            'unknown_fraction': statuses['UNKNOWN'] / count if count else None,
            'reported_usage_sum': {k: sum(c['usage'][k] for c in known)
                                   for k in ('prompt_tokens', 'completion_tokens', 'total_tokens')},
            'usage_reported_calls': len(known),
            'usage_unavailable_call_ids': [c['call_id'] for c in calls if c['usage'] is None],
            'usage_complete': bool(count) and len(known) == count,
            'known_attempt_seconds_sum': sum(elapsed),
            'elapsed_unavailable_call_ids': [c['call_id'] for c in calls if c['elapsed_seconds'] is None],
            'time_scope': 'Sum of reported attempt durations, not parallel batch wall-clock time',
            'quality_gate_affected': False, 'calls': calls}


def main():
    import argparse
    import sqlite3
    from pathlib import Path
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('ledger', type=Path)
    parser.add_argument('batch')
    parser.add_argument('output', type=Path)
    args = parser.parse_args()
    # A missing path is an error; never create another database.
    with sqlite3.connect(args.ledger.resolve().as_uri() + '?mode=ro', uri=True) as db:
        db.row_factory = sqlite3.Row
        rows = [dict(r) for r in db.execute(
            "SELECT id,status,body FROM model_calls WHERE json_extract(body,'$.batch')=?", (args.batch,))]
    report = summarize_calls(rows, args.batch)
    with args.output.open('x', encoding='utf-8') as stream:
        json.dump(report, stream, ensure_ascii=False, indent=2)
    print('Attempts:', report['attempt_count'], 'reported usage:', report['usage_reported_calls'],
          'unknown:', report['statuses'].get('UNKNOWN', 0))


if __name__ == '__main__':
    main()
