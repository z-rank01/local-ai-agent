"""One existing model ledger; no prompts, source bodies or generated text."""
from contextlib import closing
from pathlib import Path
import json
import sqlite3
import uuid


class RecordedRequest(RuntimeError):
    def __init__(self, call_id, status, conflict):
        self.call_id, self.status, self.conflict = call_id, status, conflict
        super().__init__('请求标识已存在，未重发模型请求' + ('；输入或模型快照已变化' if conflict else ''))


class CallBudget:
    def __init__(self, path, limit=0):
        self.path, self.limit = str(path), limit

    def status(self):
        if not Path(self.path).exists():
            return {'used':0, 'limit':self.limit}
        with closing(sqlite3.connect(self.path)) as db:
            used = db.execute('SELECT COUNT(*) FROM calls').fetchone()[0]
        return {'used':used, 'limit':self.limit}

    def reserve(self, model):
        if not self.limit:
            return
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        with closing(sqlite3.connect(self.path, timeout=20)) as db, db:
            db.execute('CREATE TABLE IF NOT EXISTS calls (id TEXT PRIMARY KEY, model TEXT, at TEXT DEFAULT CURRENT_TIMESTAMP)')
            db.execute('BEGIN IMMEDIATE')
            used = db.execute('SELECT COUNT(*) FROM calls').fetchone()[0]
            if used >= self.limit:
                raise RuntimeError(f'本批模型调用已达到 {self.limit} 次，已停止调用；已有结果保留。')
            db.execute('INSERT INTO calls(id,model) VALUES (?,?)', (uuid.uuid4().hex, model))

    def begin_research(self, model, execution_hash, input_hash, parameters, *, request_key=None):
        """Audit authorized research in the existing ledger, without a cumulative cost gate."""
        path = Path(self.path)
        path.parent.mkdir(parents=True, exist_ok=True)
        existed = path.exists()
        call_id = request_key or uuid.uuid4().hex
        with closing(sqlite3.connect(self.path, timeout=20)) as db:
            installed = db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='research_receipts'").fetchone()
            if existed and not installed:
                backup = path.with_name(path.stem + '.before-research.sqlite')
                # One BFF process owns the ledger; this synchronous migration precedes outbound IO.
                if not backup.exists():
                    temporary = backup.with_name('.ledger-backup-' + uuid.uuid4().hex + '.sqlite')
                    with closing(sqlite3.connect(str(temporary))) as destination:
                        db.backup(destination)
                        if destination.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
                            raise RuntimeError('模型账本备份校验失败，未执行研究请求')
                    temporary.replace(backup)
            with db:
                db.execute('BEGIN IMMEDIATE')
                db.execute('CREATE TABLE IF NOT EXISTS calls (id TEXT PRIMARY KEY, model TEXT, at TEXT DEFAULT CURRENT_TIMESTAMP)')
                db.execute('CREATE TABLE IF NOT EXISTS research_receipts ('
                           'call_id TEXT PRIMARY KEY, execution_hash TEXT NOT NULL, input_hash TEXT NOT NULL, '
                           'status TEXT NOT NULL, receipt TEXT NOT NULL, updated_at TEXT DEFAULT CURRENT_TIMESTAMP)')
                previous = db.execute('SELECT status,execution_hash,input_hash FROM research_receipts WHERE call_id=?', (call_id,)).fetchone()
                if previous:
                    raise RecordedRequest(call_id, previous[0], previous[1:] != (execution_hash, input_hash))
                db.execute('INSERT INTO calls(id,model) VALUES (?,?)', (call_id, model))
                db.execute('INSERT INTO research_receipts(call_id,execution_hash,input_hash,status,receipt) '
                           'VALUES (?,?,?,?,?)', (call_id, execution_hash, input_hash, 'RUNNING',
                            json.dumps({'model_params': parameters}, ensure_ascii=False)))
        return call_id

    def finish_research(self, call_id, receipt):
        # Never save prompts, source text, generated text, credentials or hidden reasoning here.
        allowed = {'status', 'execution_hash', 'model_params', 'finish_reason', 'response_id',
                   'response_model', 'usage', 'usage_status', 'error', 'http_status',
                   'rejection_category', 'failure_category', 'input_chars'}
        public_receipt = {key: value for key, value in receipt.items() if key in allowed}
        from core.research_errors import safe_error_code, safe_request_id
        for field, validator in (('provider_error_code', safe_error_code), ('provider_request_id', safe_request_id)):
            checked = validator(receipt.get(field))
            if checked is not None:
                public_receipt[field] = checked
        if receipt.get('provider_request_id_status') in ('REPORTED', 'CONFLICT', 'NOT_REPORTED'):
            public_receipt['provider_request_id_status'] = receipt['provider_request_id_status']
        with closing(sqlite3.connect(self.path, timeout=20)) as db, db:
            db.execute('UPDATE research_receipts SET status=?,receipt=?,updated_at=CURRENT_TIMESTAMP WHERE call_id=?',
                       (receipt['status'], json.dumps(public_receipt, ensure_ascii=False), call_id))


