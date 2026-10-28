"""Assemble content-review sheets from saved harness evidence and record verdicts.

Sheets are deterministic views of already-saved evidence (prompt, engineering
checks, tool trajectory, assistant answers); no model calls and no replay.
Verdicts are written as ``<capability>-<task>-review.json`` next to the evidence
so ``scripts/harness_holdout.py <role> --summary`` can clear content_review_pending.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HOLDOUT = ROOT / 'data/private/harness-holdout'
VERDICTS = ('COMPLETED', 'FAILED')


def case_path(root: Path, role: str, variant: str, case: str) -> Path:
    if not case or '/' in case or '\\' in case or '..' in case:
        raise ValueError('case 必须形如 meeting-notes-m1')
    return root / role / variant / (case + '.json')


def review_path_for(root: Path, role: str, variant: str, case: str) -> Path:
    return case_path(root, role, variant, case).with_name(case + '-review.json')


def load_case(root: Path, role: str, variant: str, case: str) -> dict:
    path = case_path(root, role, variant, case)
    if not path.is_file():
        raise SystemExit(f'证据文件不存在：{path}')
    return json.loads(path.read_text(encoding='utf-8'))


def iter_cases(root: Path, role: str, variant: str | None):
    role_dir = root / role
    if not role_dir.is_dir():
        return
    for variant_dir in sorted(role_dir.glob('v*')):
        if variant and variant_dir.name != variant:
            continue
        for evidence in sorted(variant_dir.glob('*.json')):
            if evidence.name in ('runs.json', 'inputs.json', 'case.json') or evidence.stem.endswith('-review'):
                continue
            yield role, variant_dir.name, evidence.stem


def read_review(root: Path, role: str, variant: str, case: str) -> dict:
    path = review_path_for(root, role, variant, case)
    return json.loads(path.read_text(encoding='utf-8')) if path.exists() else {}


def _preview(value, limit: int) -> str:
    text = ' '.join(str(value).split())
    return text if len(text) <= limit else text[:limit] + '…'


def build_sheet(case: dict) -> str:
    run = case.get('run') or {}
    checks = case.get('checks') or {}
    lines = [
        f"=== {case.get('variant')}/{case.get('key')} ===",
        f"model: {case.get('model')}   stream: {case.get('status')}   run: {run.get('status')}   error: {run.get('error') or '-'}",
        '',
        'engineering checks:',
        *[f'  {key}: {value}' for key, value in checks.items() if not isinstance(value, (list, dict))],
    ]
    for key, value in checks.items():
        if isinstance(value, (list, dict)):
            lines.append(f'  {key}: {_preview(json.dumps(value, ensure_ascii=False), 240)}')
    lines += ['', 'prompt:', '  ' + (case.get('prompt') or '').replace('\n', '\n  ')]
    calls = case.get('tool_calls') or []
    lines += ['', f'tool trajectory ({len(calls)} calls):']
    for index, call in enumerate(calls, 1):
        name = call.get('tool_name') or '(call)'
        params = _preview(json.dumps(call.get('params') or {}, ensure_ascii=False), 120)
        lines.append(f'  {index}. {name}({params}) [{call.get("status")}]')
        content = call.get('content')
        if content:
            lines.append(f'     → {_preview(content, 160)}')
    answers = case.get('assistant') or []
    lines += ['', f'assistant messages ({len(answers)}, last is final):']
    for index, message in enumerate(answers, 1):
        marker = 'FINAL' if index == len(answers) else f'#{index}'
        lines.append(f'--- [{marker}] ({len(message)} chars) ---')
        lines.append(message)
    return '\n'.join(lines)


def write_review(root: Path, role: str, variant: str, case: str, status: str,
                 issues: list[str], notes: str) -> Path:
    if status not in VERDICTS:
        raise ValueError(f'status 必须是 {" 或 ".join(VERDICTS)}')
    load_case(root, role, variant, case)  # 拒绝给不存在的证据写核对
    path = review_path_for(root, role, variant, case)
    record = {
        'status': status,
        'reviewed_at': datetime.now(timezone.utc).isoformat(),
        'reviewer': 'human',
        'case': f'{role}/{variant}/{case}',
        'issues': issues,
        'notes': notes or '',
    }
    path.write_text(json.dumps(record, ensure_ascii=False, indent=1), encoding='utf-8')
    return path


def sample_keys(root: Path):
    path = root / 'engineering-samples.json'
    if not path.is_file():
        return []
    samples = json.loads(path.read_text(encoding='utf-8'))
    return [(key.split(':', 1)[0], value.get('variant'), key.split(':', 1)[1].replace('/', '-'))
            for key, value in samples.get('cases', {}).items()]


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)

    listing = commands.add_parser('list', help='列出用例与核对状态')
    listing.add_argument('--role', choices=('local', 'cloud'))
    listing.add_argument('--variant')
    listing.add_argument('--pending-only', action='store_true')
    listing.add_argument('--from-samples', action='store_true', help='只列 engineering-samples 精选集')

    sheet = commands.add_parser('sheet', help='打印单条核对清单')
    sheet.add_argument('role', choices=('local', 'cloud'))
    sheet.add_argument('variant')
    sheet.add_argument('case', help='形如 meeting-notes-m1')

    record = commands.add_parser('record', help='记录人工核对结论')
    record.add_argument('role', choices=('local', 'cloud'))
    record.add_argument('variant')
    record.add_argument('case')
    record.add_argument('--status', required=True, choices=VERDICTS)
    record.add_argument('--issue', action='append', default=[], help='问题清单，可重复')
    record.add_argument('--notes', default='')

    args = parser.parse_args(argv)
    if args.command == 'list':
        rows = (sample_keys(HOLDOUT) if args.from_samples
                else iter_cases(HOLDOUT, args.role or '*', args.variant))
        for role, variant, case in rows:
            if args.role and role != args.role:
                continue
            review = read_review(HOLDOUT, role, variant, case)
            status = review.get('status', 'PENDING')
            if args.pending_only and status != 'PENDING':
                continue
            issues = '; '.join(review.get('issues', [])) or '-'
            print(f'{role}/{variant}/{case}  [{status}]  issues: {issues}')
    elif args.command == 'sheet':
        print(build_sheet(load_case(HOLDOUT, args.role, args.variant, args.case)))
    elif args.command == 'record':
        path = write_review(HOLDOUT, args.role, args.variant, args.case,
                            args.status, args.issue, args.notes)
        print(path)


if __name__ == '__main__':
    sys.exit(main())
