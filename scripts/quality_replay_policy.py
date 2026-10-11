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
