"""Fixed-evidence replay policies; no transport, credentials or model calls here.

The caller supplies the connected stock repository on sys.path and frozen
materials. Scoring and UNKNOWN handling remain outside this policy module.
"""

POLICY_VERSION = 'quality-replay-v5'


def validate_result(registry, request, parsed, *, arm, final_repair=False):
    from pydantic import ValidationError
    from services.agents.contracts import validate_output
    from services.paper.common import RuleError

    if arm not in ('original', 'quality'):
        raise ValueError('Unknown comparison arm')
    try:
        return validate_output(registry, request, parsed).model_dump(mode='json')
    except (RuleError, ValidationError, ValueError):
        if (arm != 'quality' or not final_repair or not isinstance(parsed, dict)
                or parsed.get('run_status') != 'COMPLETED'):
            raise
        if request.duty_id in ('C01', 'C02'):
            from services.paper.theme_research import isolate_theme_output
            partial = isolate_theme_output(parsed, request.material, request.duty_id)
        elif request.duty_id == 'C03':
            from services.paper.company_research import isolate_invalid_analysis
            partial = isolate_invalid_analysis(parsed, {
                **request.material, '_evidence_ids': request.evidence_ids})
        else:
            raise
        return validate_output(registry, request, partial).model_dump(mode='json')


def theme_runner(runner, data, job, *, arm):
    """Original single request vs production hierarchical reading, same evidence."""
    if arm == 'original':
        return runner
    if arm != 'quality':
        raise ValueError('Unknown comparison arm')
    from services.paper.theme_reading import ThemeReadingRunner
    return ThemeReadingRunner(runner, data, job)


def execute_fixed_case(runner, case, sample, execution, root):
    """Run frozen evidence through production orchestration using injected transport.

    The caller owns admission (including historical UNKNOWN isolation), the frozen
    bundle check, node receipts, cancellation and validation via validate_result.
    This function does not acquire evidence or create a service/database.
    """
    import json
    from pathlib import Path
    from types import SimpleNamespace
    from services.paper.common import RuleError, digest
    from services.paper.data import atomic_json

    arm, duty = sample['arm'], case['role']
    if arm not in ('original', 'quality') or duty not in ('C01', 'C02', 'C03'):
        raise ValueError('Unsupported fixed-evidence treatment or duty')
    if digest(case['material']) != case['input_hash']:
        raise RuleError('Fixed evidence changed after freezing')
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    rid = 'quality-fixed:' + sample['blind_id']
    binding = {'arm': arm, 'duty': duty, 'input_hash': case['input_hash'],
               'case_hash': digest(case), 'execution_snapshot_hash': digest(execution),
               'execution_hash': execution['execution_hash'],
               'policy_version': POLICY_VERSION}
    checkpoint = root / ('execution-' + digest(rid) + '.json')
    saved = json.loads(checkpoint.read_text(encoding='utf-8')) if checkpoint.exists() else {
        'binding': binding, 'company': {'quality_first': arm == 'quality',
                                      'input_chars': execution['input_chars']}}
    if saved['binding'] != binding:
        raise RuleError('Frozen replay checkpoint does not match this treatment')
    def save():
        atomic_json(checkpoint, saved)
    runner.check_cancelled(rid)
    save()
    if duty == 'C03':
        from services.paper.company_research import run_company
        code = case['material']['instrument']['symbol']
        return run_company(runner, case['material'], saved['company'], save,
                           subject=code, job_id=rid, code=code, round_index=1)
    job = {'id': rid, 'payload': {'research_execution': execution}}
    selected = theme_runner(runner, SimpleNamespace(root=root), job, arm=arm)
    return selected.run(duty, case['material'], request_id=rid,
                        research_id=rid, subject_id='public-replay',
                        evidence_ids=case['evidence_ids']).output.model_dump(mode='json')
