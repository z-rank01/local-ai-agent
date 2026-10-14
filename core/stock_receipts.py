"""Pair successful stock tool receipts without interpreting research content."""
import json
import re
from dataclasses import dataclass

KINDS = {
    'stock_report_read': {'market_brief_page', 'local_report_page'},
    'stock_public_evidence_find': {'public_evidence_find'},
    'stock_public_evidence_read': {'public_evidence_read'},
}
FAILED = {'FAILED', 'RULE_BLOCKED', 'ERROR'}


def successful(value):
    return (isinstance(value, dict) and not value.get('error')
            and str(value.get('status', '')).upper() not in FAILED)


@dataclass(frozen=True)
class Receipt:
    position: int
    reference: str
    tool_name: str
    params: dict
    message: dict
    observation: dict


def paired_receipts(messages, tools=None, *, allow_legacy_reference=True, _calls=None, _offset=0):
    """Yield same-call, same-tool receipts; conflicting call IDs are unusable.

    This validates protocol provenance only. Consumers still validate sources,
    financial periods, versions and calculation fields independently.
    """
    allowed = set(KINDS) if tools is None else set(tools)
    calls = {} if _calls is None else _calls
    for position, message in enumerate(messages, _offset):
        if message.get('role') == 'assistant':
            for call in message.get('tool_calls') or []:
                if not isinstance(call, dict):
                    continue
                fn = call.get('function') or {}
                if not isinstance(fn, dict) or fn.get('name') not in allowed:
                    continue
                call_id = call.get('id')
                if not isinstance(call_id, str) or not call_id:
                    continue
                try:
                    params = fn.get('arguments', {})
                    params = json.loads(params) if isinstance(params, str) else params
                    reference = params.get('reference') if isinstance(params, dict) else None
                    if not isinstance(reference, str) or not re.fullmatch('[0-9a-f]{64}', reference):
                        continue
                except (TypeError, ValueError):
                    continue
                pair = (fn['name'], reference, params)
                calls[call_id] = pair if call_id not in calls or calls[call_id] == pair else None
        if message.get('role') != 'tool' or message.get('tool_name') not in allowed:
            continue
        pair = calls.get(message.get('tool_call_id'))
        if pair is None or pair[0] != message['tool_name']:
            continue
        try:
            envelope = json.loads(message.get('content') or '{}')
        except (TypeError, ValueError):
            continue
        if not successful(envelope):
            continue
        value = envelope.get('model_observation', envelope)
        if not successful(value) or value.get('kind') not in KINDS[pair[0]]:
            continue
        reference = value.get('source_reference', pair[1] if allow_legacy_reference else None)
        if reference != pair[1]:
            continue
        yield Receipt(position, reference, pair[0], pair[2], message, value)
