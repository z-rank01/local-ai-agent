"""Capability installation state in the existing conversation database."""
from datetime import datetime, timezone
import json


class CapabilityStore:
    def __init__(self, connect):
        self.connect = connect

    def list(self, workspace_id, kind='skill'):
        with self.connect() as db:
            return {row['capability_id']: json.loads(row['state']) for row in db.execute(
                'SELECT capability_id,state FROM capability_installs WHERE workspace_id=? AND kind=?',
                (workspace_id, kind))}

    def save(self, workspace_id, capability_id, state, kind='skill'):
        with self.connect() as db:
            db.execute('INSERT INTO capability_installs VALUES(?,?,?,?,?) '
                       'ON CONFLICT(workspace_id,capability_id,kind) DO UPDATE SET '
                       'state=excluded.state,updated_at=excluded.updated_at',
                       (workspace_id, capability_id, kind, json.dumps(state, ensure_ascii=False),
                        datetime.now(timezone.utc).isoformat()))

    def remove(self, workspace_id, capability_id, kind='skill'):
        with self.connect() as db:
            db.execute('DELETE FROM capability_installs WHERE workspace_id=? AND capability_id=? AND kind=?',
                       (workspace_id, capability_id, kind))
