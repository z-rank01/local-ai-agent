"""Generic evidence projection and content identity helpers."""
from copy import deepcopy
from hashlib import sha256
import json


def model_evidence(index):
    index = deepcopy(index)
    for source in index.get('sources', {}).values():
        texts = list(dict.fromkeys(source.get('received', [])))
        source['received'] = [text for text in texts
                              if not any(text != other and text in other for other in texts)]
    return index


def fingerprint(candidate):
    return sha256(json.dumps(candidate, ensure_ascii=False, sort_keys=True,
                             allow_nan=False).encode()).hexdigest()
