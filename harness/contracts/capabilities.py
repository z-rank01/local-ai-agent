"""Serializable metadata shared by capability providers and their clients."""
from dataclasses import dataclass

_MAX_CAPABILITY_IDS = 64
_MAX_CAPABILITY_ID_LENGTH = 120
_CAPABILITY_KINDS = frozenset({'skill', 'plugin'})


@dataclass(frozen=True, slots=True)
class CapabilityDescriptor:
    id: str
    version: str
    kind: str
    title: str
    description: str
    required: tuple[str, ...] = ()
    optional: tuple[str, ...] = ()
    permissions: tuple[str, ...] = ()


def normalize_capability_ids(values) -> list[str]:
    """Validate ``skill:<name>`` / ``plugin:<id>`` conversation selections.

    Order is preserved and duplicate identities are dropped. The service maps
    ``ValueError`` to a 422 response; the store keeps the normalized JSON list.
    """
    if values is None:
        return []
    if not isinstance(values, (list, tuple)):
        raise ValueError('能力选择格式无效')
    result: list[str] = []
    seen: set[str] = set()
    for raw in values:
        if not isinstance(raw, str):
            raise ValueError('能力选择格式无效')
        identity = raw.strip()
        if not identity or len(identity) > _MAX_CAPABILITY_ID_LENGTH:
            raise ValueError('能力选择格式无效')
        kind, separator, name = identity.partition(':')
        if separator != ':' or kind not in _CAPABILITY_KINDS or not name.strip():
            raise ValueError('能力选择格式无效')
        if identity in seen:
            continue
        seen.add(identity)
        result.append(identity)
    if len(result) > _MAX_CAPABILITY_IDS:
        raise ValueError(f'最多选择 {_MAX_CAPABILITY_IDS} 项能力')
    return result


def split_capability_id(identity: str) -> tuple[str, str] | None:
    """Return ``(kind, name)`` for a canonical identity, or ``None``."""
    if not isinstance(identity, str):
        return None
    kind, separator, name = identity.partition(':')
    if separator != ':' or kind not in _CAPABILITY_KINDS or not name:
        return None
    return kind, name
