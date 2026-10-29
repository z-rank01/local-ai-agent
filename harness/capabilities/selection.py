"""Per-run capability focus derived from a conversation selection.

Only the advertised Skill/Plugin catalog is narrowed. Tool registration,
permissions and plugin lifecycle stay host-owned and unchanged.
"""
from __future__ import annotations

from typing import Any

from harness.contracts.capabilities import split_capability_id


def filter_capability_snapshot(snapshot: dict[str, Any], capability_ids) -> dict[str, Any]:
    """Return a catalog copy restricted to selected capabilities.

    An empty selection means "no explicit focus" and returns the snapshot
    unchanged, preserving the pre-selection behaviour for existing sessions.
    Unknown or unavailable identities are simply absent from the result.
    """
    if not capability_ids:
        return snapshot
    skills: set[str] = set()
    plugins: set[str] = set()
    for identity in capability_ids:
        parsed = split_capability_id(identity)
        if parsed is None:
            continue
        kind, name = parsed
        if kind == 'skill':
            skills.add(name)
        else:
            plugins.add(name)
    filtered = dict(snapshot)
    filtered['versions'] = {
        name: entry
        for name, entry in (snapshot.get('versions') or {}).items()
        if name in skills
    }
    filtered['capabilities'] = [
        row
        for row in (snapshot.get('capabilities') or [])
        if isinstance(row, dict) and row.get('id') in plugins
    ]
    return filtered
