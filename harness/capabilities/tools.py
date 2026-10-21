"""Atomic, owner-scoped tool registration without knowledge of any domain."""
from copy import deepcopy
from collections.abc import Iterable


class OwnedTools:
    def __init__(self):
        self._entries: dict[str, dict] = {}
        self._owners: dict[str, str] = {}

    def replace(self, owner: str, definitions: Iterable[dict]) -> None:
        if not owner:
            raise ValueError('Tool registration requires an owner')
        pending = {}
        for original in definitions:
            tool = deepcopy(original)
            name = tool.get('name')
            if not isinstance(name, str) or not name or not tool.get('backend'):
                raise ValueError('Tool registration requires a name and backend')
            if name in pending:
                raise ValueError(f'Duplicate tool in registration: {name}')
            previous = self._owners.get(name)
            if previous is not None and previous != owner:
                raise ValueError(f'Tool already registered by {previous}: {name}')
            pending[name] = tool
        # Validate the whole contribution before removing its previous revision.
        entries = {k: v for k, v in self._entries.items() if self._owners[k] != owner}
        owners = {k: v for k, v in self._owners.items() if v != owner}
        entries.update(pending)
        owners.update({name: owner for name in pending})
        self._entries, self._owners = entries, owners

    def snapshot(self) -> dict[str, dict]:
        return deepcopy(self._entries)

    def owners(self) -> dict[str, str]:
        return dict(self._owners)
