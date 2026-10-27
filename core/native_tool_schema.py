"""Local fingerprints of tool schemas, with optional declared reply purposes."""
from __future__ import annotations

import hashlib
import json
import re

VERSION = 'native-tool-schema-v1'


def fingerprint(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                    separators=(',', ':')).encode()).hexdigest()


def tool_schema_observation(payload, purposes=None):
    """Inspect the final payload without changing it or inventing tool metadata."""
    purposes = purposes or {}
    tools = payload.get('tools', [])
    base = {'tool_schema_observation_version': VERSION,
            'tools_field_present': 'tools' in payload}
    if not isinstance(tools, list):
        return {**base, 'tool_schema_status': 'UNAVAILABLE'}
    try:
        rows = []
        for tool in tools:
            function = tool.get('function') if isinstance(tool, dict) else None
            if not isinstance(function, dict):
                return {**base, 'tool_schema_status': 'UNAVAILABLE'}
            name = function.get('name')
            if not isinstance(name, str) or not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]{0,127}', name):
                return {**base, 'tool_schema_status': 'UNAVAILABLE'}
            row = {'name': name, 'function_sha256': fingerprint(function),
                   'parameters_sha256': fingerprint(function.get('parameters')),
                   'description_sha256': fingerprint(function.get('description'))}
            if name in purposes:
                params = function.get('parameters')
                props = params.get('properties') if isinstance(params, dict) else None
                field = props.get('reply_focus') if isinstance(props, dict) else None
                purpose = {'status': 'MISSING'}
                if field is not None:
                    purpose = {'status': 'UNRECOGNISED', 'field_sha256': fingerprint(field)}
                    enum = field.get('enum') if isinstance(field, dict) else None
                    if (isinstance(field, dict) and field.get('type') == 'string'
                            and isinstance(enum, list) and enum and all(
                                isinstance(v, str) and v in purposes[name] for v in enum)):
                        purpose.update(status='REPORTED', enum=list(enum))
                row['reply_focus_schema'] = purpose
            rows.append(row)
        return {**base, 'tool_schema_status': 'REPORTED',
                'tool_schema_count': len(tools), 'tools_sha256': fingerprint(tools),
                'tool_schema_fingerprints': rows}
    except (TypeError, ValueError):
        return {**base, 'tool_schema_status': 'UNAVAILABLE'}
