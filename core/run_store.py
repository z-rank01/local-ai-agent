"""Durable execution status in the existing conversation database."""
import json
from datetime import datetime, timezone

TERMINAL = frozenset({'SUCCEEDED', 'FAILED', 'STOPPED', 'INTERRUPTED'})


def now():
    return datetime.now(timezone.utc).isoformat()


class RunStore:
    def __init__(self, store):
        self.store = store

    @staticmethod
    def decode(row):
        if row is None:
            return None
        result = dict(row)
        for key in ('payload', 'checkpoint'):
            result[key] = json.loads(result[key])
        return result

    def get(self, run_id):
        with self.store._connect() as db:
            return self.decode(db.execute('SELECT * FROM chat_runs WHERE id=?', (run_id,)).fetchone())

    def by_request(self, request_id):
        if not request_id:
            return None
        with self.store._connect() as db:
            return self.decode(db.execute('SELECT * FROM chat_runs WHERE request_id=?', (request_id,)).fetchone())

    def create(self, run_id, *, workspace_id, conversation_id, request_id, kind, payload, resume_of=None):
        stamp = now()
        with self.store._connect() as db:
            db.execute('INSERT INTO chat_runs(id,workspace_id,conversation_id,request_id,kind,status,payload,resume_of,created_at,updated_at) '
                       'VALUES(?,?,?,?,?,?,?,?,?,?)',
                       (run_id, workspace_id, conversation_id, request_id, kind, 'RUNNING',
                        json.dumps(payload, ensure_ascii=False), resume_of, stamp, stamp))
        return self.get(run_id)

    def list(self, workspace_id=None, limit=100):
        # Sort lightweight IDs, not multi-megabyte research checkpoints. Sorting
        # full rows spills private payloads to SQLite's OS temporary directory.
        sql, args = 'SELECT id FROM chat_runs', []
        if workspace_id:
            sql += ' WHERE workspace_id=?'
            args.append(workspace_id)
        sql += " ORDER BY CASE WHEN status IN ('RUNNING','PAUSING','PAUSED','STOPPING') THEN 0 ELSE 1 END, updated_at DESC LIMIT ?"
        args.append(limit)
        with self.store._connect() as db:
            ids = [row['id'] for row in db.execute(sql, args)]
            return [self.decode(db.execute('SELECT * FROM chat_runs WHERE id=?', (run_id,)).fetchone())
                    for run_id in ids]

    def update(self, run_id, **fields):
        allowed = {'conversation_id', 'status', 'model', 'phase', 'error', 'checkpoint', 'payload'}
        if fields.keys() - allowed:
            raise ValueError('Unsupported run field')
        for key in ('checkpoint', 'payload'):
            if key in fields:
                fields[key] = json.dumps(fields[key], ensure_ascii=False)
        fields['updated_at'] = now()
        with self.store._connect() as db:
            db.execute('UPDATE chat_runs SET ' + ','.join(k+'=?' for k in fields) + ' WHERE id=?',
                       [*fields.values(), run_id])

    def append(self, run_id, event):
        with self.store._connect() as db:
            db.execute('BEGIN IMMEDIATE')
            seq = db.execute('SELECT last_seq FROM chat_runs WHERE id=?', (run_id,)).fetchone()[0] + 1
            event = {**event, 'run_id': run_id, 'seq': seq}
            db.execute('INSERT INTO chat_run_events VALUES(?,?,?)',
                       (run_id, seq, json.dumps(event, ensure_ascii=False)))
            db.execute('UPDATE chat_runs SET last_seq=?,updated_at=? WHERE id=?', (seq, now(), run_id))
        return event

    def events(self, run_id, after_seq=0):
        with self.store._connect() as db:
            return [json.loads(row[0]) for row in db.execute(
                'SELECT payload FROM chat_run_events WHERE run_id=? AND seq>? ORDER BY seq',
                (run_id, after_seq))]

    def interrupt_orphans(self):
        with self.store._connect() as db:
            db.execute("UPDATE chat_runs SET status='INTERRUPTED',error='服务已重启；请核对检查点后继续',updated_at=? "
                       "WHERE status IN ('RUNNING','PAUSING','PAUSED','STOPPING')", (now(),))
