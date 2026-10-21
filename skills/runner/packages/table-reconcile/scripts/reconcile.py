"""Read-only, decimal reconciliation of UTF-8 CSV/TSV tables."""
import argparse
import csv
import hashlib
import io
import json
from decimal import Decimal, InvalidOperation, localcontext
from pathlib import Path


def read_table(path, key, columns):
    raw = path.read_bytes()
    reader = csv.DictReader(io.StringIO(raw.decode('utf-8-sig'), newline=''),
                            delimiter='\t' if path.suffix.lower() == '.tsv' else ',')
    fields = reader.fieldnames or []
    if len(fields) != len(set(fields)) or not {key, *columns}.issubset(fields):
        raise ValueError(f'{path.name}: 表头重复或缺少所需字段')
    rows, issues = {}, []
    for record, row in enumerate(reader, 2):
        if None in row or any(value is None for value in row.values()):
            raise ValueError(f'{path.name}: 第 {record} 条记录列数与表头不符')
        identity = row[key].strip()
        if not identity:
            issues.append({'record': record, 'reason': 'empty_key'})
        else:
            rows.setdefault(identity, []).append({'record': record, 'values': row})
    return rows, issues, {'name': str(path), 'sha256': hashlib.sha256(raw).hexdigest()}


def number(value):
    try:
        result = Decimal(value.strip())
    except InvalidOperation as exc:
        raise ValueError('非有效十进制数值') from exc
    if not result.is_finite():
        raise ValueError('非有限数值')
    return result


def reconcile(left, right, key, columns, tolerance=Decimal('0')):
    if not tolerance.is_finite() or tolerance < 0:
        raise ValueError('容差必须为有限非负数')
    a, a_issues, a_source = read_table(left, key, columns)
    b, b_issues, b_source = read_table(right, key, columns)
    changes, unresolved, matched = [], [], 0
    for identity in sorted(a.keys() | b.keys()):
        x, y = a.get(identity, []), b.get(identity, [])
        if len(x) > 1 or len(y) > 1:
            unresolved.append({'key': identity, 'reason': 'duplicate_key',
                               'left_records': [r['record'] for r in x],
                               'right_records': [r['record'] for r in y]})
            continue
        if not x or not y:
            changes.append({'key': identity, 'reason': 'missing_left' if not x else 'missing_right'})
            continue
        matched += 1
        for column in columns:
            values = {'left': x[0]['values'][column], 'right': y[0]['values'][column]}
            item = {'key': identity, 'column': column, **values,
                    'left_record': x[0]['record'], 'right_record': y[0]['record']}
            try:
                lhs, rhs = number(values['left']), number(values['right'])
                with localcontext() as context:
                    context.prec = max(lhs.adjusted(), rhs.adjusted()) - min(
                        lhs.as_tuple().exponent, rhs.as_tuple().exponent) + 3
                    delta = rhs - lhs
            except ValueError:
                unresolved.append({**item, 'reason': 'invalid_number'})
                continue
            if delta.copy_abs() > tolerance:
                changes.append({**item, 'reason': 'value_difference', 'right_minus_left': str(delta)})
    unresolved.extend({'side': side, **issue} for side, issues in
                      [('left', a_issues), ('right', b_issues)] for issue in issues)
    return {'status': 'needs_review' if unresolved else ('different' if changes else 'equal'),
            'sources': [a_source, b_source], 'key_column': key, 'value_columns': columns,
            'tolerance': str(tolerance), 'matched_keys': matched,
            'differences': changes, 'unresolved': unresolved}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('left', type=Path)
    parser.add_argument('right', type=Path)
    parser.add_argument('--key', required=True)
    parser.add_argument('--columns', nargs='+', required=True)
    parser.add_argument('--tolerance', default='0')
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    try:
        result = reconcile(args.left, args.right, args.key, args.columns, number(args.tolerance))
        # Exclusive creation protects original inputs and previous deliverables.
        with args.output.open('x', encoding='utf-8') as out:
            json.dump(result, out, ensure_ascii=False, indent=2)
    except (ValueError, OSError) as exc:
        parser.exit(2, f'{exc}\n')
    print(json.dumps({'status': result['status'], 'output': str(args.output)}, ensure_ascii=False))


if __name__ == '__main__':
    main()
