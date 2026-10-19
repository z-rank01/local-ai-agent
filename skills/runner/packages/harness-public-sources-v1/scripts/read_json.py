"""Select exact JSON objects from a web_fetch saved_source, without downloading data.

Return the existing source_excerpts receipt plus ready-to-copy publication sources.
Requested fields keep provider names and values. Missing data is never replaced by zero.
"""
import argparse
import json
from pathlib import Path
import re


def read_rows(path, where, fields, limit=2):
    document = json.loads(Path(path).read_text(encoding='utf8'))
    decoder = json.JSONDecoder()
    rows, excerpts, sources = [], [], []
    for page in document['pages']:
        text = page['text']
        for match in re.finditer(r'\{', text):
            try:
                row, end = decoder.raw_decode(text, match.start())
            except ValueError:
                continue
            if not isinstance(row, dict) or not all(k in row and row[k] == v for k, v in where.items()):
                continue
            quote = text[match.start():end]
            source_id = Path(path).stem
            locator = f"第{page['page']}页，JSON字符{match.start()}—{end}；" + json.dumps(where, ensure_ascii=False)
            excerpts.append({'source_id': source_id, 'sha256': document['sha256'],
                'page': page['page'], 'start': match.start(), 'end': end, 'quote': quote})
            sources.append({'id': source_id, 'locator': locator, 'quote': quote})
            rows.append({'values': {k: row[k] for k in fields if k in row},
                         'missing_fields': [k for k in fields if k not in row]})
            if len(rows) == limit:
                return {'rows': rows, 'source_excerpts': excerpts, 'sources': sources}
    return {'rows': rows, 'source_excerpts': excerpts, 'sources': sources}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('path')
    parser.add_argument('--where', nargs='+', required=True, help='Exact provider key=value; use the observed date format')
    parser.add_argument('--fields', nargs='+', required=True)
    parser.add_argument('--limit', type=int, choices=range(1, 7), default=2)
    args = parser.parse_args()
    where = dict(item.split('=', 1) for item in args.where)
    print(json.dumps(read_rows(args.path, where, args.fields, args.limit), ensure_ascii=False))
