"""Batch exact excerpts from web_fetch's saved_source JSON, without networking.

python extract.py /workspace/.../sources/S....json --terms 营业收入 归属于 --limit 4
The output is a reading receipt, not an assertion that the excerpt proves a claim.
"""
import argparse
import json
from pathlib import Path


def extract(path, terms, limit=4, context=240):
    document = json.loads(Path(path).read_text(encoding='utf-8'))
    found = []
    for page in document['pages']:
        text = page['text']
        for term in terms:
            start = text.find(term)
            if start < 0:
                continue
            start, end = max(0, start-context), min(len(text), start+len(term)+context)
            if any(row['page'] == page['page'] and row['start'] <= start < row['end'] for row in found):
                continue
            found.append({'source_id': Path(path).stem, 'sha256': document['sha256'],
                          'page': page['page'], 'start': start, 'end': end, 'quote': text[start:end]})
            if len(found) == limit:
                return {'source_excerpts': found}
    return {'source_excerpts': found, 'unmatched_terms': [term for term in terms
            if not any(term in row['quote'] for row in found)]}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('path', help='Use the exact saved_source path returned by web_fetch')
    parser.add_argument('--terms', nargs='+', required=True)
    parser.add_argument('--limit', type=int, choices=range(1, 7), default=4)
    parser.add_argument('--context', type=int, choices=range(40, 401), default=240)
    args = parser.parse_args()
    print(json.dumps(extract(args.path, args.terms, args.limit, args.context), ensure_ascii=False))
