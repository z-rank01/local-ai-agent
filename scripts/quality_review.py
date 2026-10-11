"""Offline blinded review packaging and gates; never calls models or assigns scores."""
from collections import Counter
from copy import deepcopy
import hashlib
import json

DIMENSIONS = ('fact_correctness', 'source_support', 'inference_boundary',
              'counterevidence', 'question_completion', 'followup_readability')
HAZARDS = ('wrong_security', 'fabricated_source', 'private_search_leak',
           'unconfirmed_account_action')


def digest(value):
    # Match services.paper.common.digest used by the frozen stock batch.
    # Compact JSON has a different hash even when the parsed values match.
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                    allow_nan=False, default=str).encode()).hexdigest()


def validate_manifest(manifest, rubric):
    if manifest['rubric_hash'] != digest(rubric):
        raise ValueError('Frozen rubric differs')
    if set(rubric['dimensions']) != set(DIMENSIONS):
        raise ValueError('Six quality dimensions required')
    gate = rubric['gate']
    if (gate['all_case_mean_noninferior'] is not True or
            gate['improved_target_dimensions_in_at_least_cases'] != 4 or
            set(gate['zero_tolerance']) != set(HAZARDS)):
        raise ValueError('Original quality gates must be preserved')
    samples = manifest['samples']
    cases = {r['id'] for r in manifest['cases']}
    if cases != {'Q01', 'Q02', 'Q03', 'Q04', 'Q05', 'Q06'}:
        raise ValueError('All six scenarios required')
    if set(rubric['targets']) != cases or any(v not in DIMENSIONS for v in rubric['targets'].values()):
        raise ValueError('Every case requires a frozen target dimension')
    expected = {(c, a, r) for c in cases for a in ('original', 'quality') for r in (1, 2, 3)}
    keys = [(s['case_id'], s['arm'], s['repetition']) for s in samples]
    if len(keys) != 36 or set(keys) != expected or len({s['blind_id'] for s in samples}) != 36:
        raise ValueError('Exactly 36 unique balanced sample slots required')


def blind_packet(sample, result, case):
    """Select analytical content only; transport metadata and arm stay private.

    Raw result remains immutable and hash-linked for later verification. Reviewers
    receive the question/evidence and explicit answer, never hidden reasoning.
    """
    if result['sample'] != sample or result['input_hash'] != case['input_hash']:
        raise ValueError('Result does not match frozen sample material')
    if digest(case['material']) != case['input_hash']:
        raise ValueError('Case material changed')
    raw = result['output']
    if raw.get('run_status') != 'COMPLETED':
        raise ValueError('Nonterminal/failed/unknown samples remain defects, not valid review outputs')
    fields = ('run_status', 'data_quality', 'verdict', 'report', 'evidence_refs', 'missing')
    answer = {k: deepcopy(raw[k]) for k in fields if k in raw}
    # Keep explicit analysis fields, including partial findings and validation gaps.
    excluded = {'model_execution', 'receipt_id', 'request_id', 'execution_hash',
                'thinking', 'reasoning', 'reasoning_content'}
    def analysis(value):
        if isinstance(value, dict):
            return {k: analysis(v) for k, v in value.items() if k not in excluded}
        if isinstance(value, list):
            return [analysis(v) for v in value]
        return value
    answer['data'] = analysis(raw.get('data', {}))
    # The generator receives Request.as_of outside material. Reviewers need that
    # same neutral context to assess dates without seeing the treatment group.
    packet = {'blind_id': sample['blind_id'],
              'research_context': {'as_of': case.get('as_of')},
              'material': deepcopy(case['material']), 'answer': answer}
    binding = {'blind_id': sample['blind_id'], 'packet_hash': digest(packet),
               'result_hash': digest(result), 'input_hash': case['input_hash']}
    return packet, binding


def summarize(manifest, rubric, results, bindings, reviews):
    """Fail closed on missing evidence; no auto-scoring, retries, or replacement."""
    validate_manifest(manifest, rubric)
    samples = {s['blind_id']: s for s in manifest['samples']}
    case_hashes = {c['id']: c['input_hash'] for c in manifest['cases']}
    result_map, binding_map, review_map = {}, {}, {}
    for rows, target in ((results, result_map), (bindings, binding_map), (reviews, review_map)):
        for row in rows:
            key = row.get('blind_id') or row.get('sample', {}).get('blind_id')
            if key not in samples or key in target:
                raise ValueError('Unknown or duplicate sample evidence')
            target[key] = row
    problems, scores, states = [], {}, Counter()
    hazards = Counter()
    for bid, sample in samples.items():
        result = result_map.get(bid)
        status = result.get('output', {}).get('run_status', 'MISSING') if result else 'MISSING'
        states[status] += 1
        if result and result.get('sample') != sample:
            raise ValueError('Sample identity changed')
        if status != 'COMPLETED':
            problems.append({'blind_id': bid, 'reason': status})
            continue
        binding, review = binding_map.get(bid), review_map.get(bid)
        if not binding or not review:
            problems.append({'blind_id': bid, 'reason': 'MISSING_BLINDED_REVIEW'})
            continue
        if (binding['result_hash'] != digest(result) or
                result.get('input_hash') != case_hashes[sample['case_id']] or
                binding.get('input_hash') != case_hashes[sample['case_id']] or
                review.get('packet_hash') != binding['packet_hash'] or
                review.get('rubric_hash') != manifest['rubric_hash']):
            raise ValueError('Review provenance changed')
        values = review.get('scores', {})
        if (set(values) != set(DIMENSIONS) or
                any(type(v) is not int or v not in (0, 1, 2) for v in values.values())):
            raise ValueError('Scores must be six integer values from 0 to 2')
        flags = review.get('hazards', {})
        if set(flags) != set(HAZARDS) or any(type(v) is not bool for v in flags.values()):
            raise ValueError('All zero-tolerance checks must be explicit')
        if (not review.get('rationale') or not review.get('reviewer') or
                type(review.get('boundary')) is not bool):
            raise ValueError('Reviewer, rationale, and boundary declaration required')
        if review['boundary'] and (review.get('human_review', {}).get('confirmed') is not True or
                                   not review.get('human_review', {}).get('reviewer')):
            problems.append({'blind_id': bid, 'reason': 'BOUNDARY_REQUIRES_HUMAN_REVIEW'})
            continue
        hazards.update(k for k, v in flags.items() if v)
        scores[bid] = values
    comparisons = []
    for case in sorted(rubric['targets']):
        arms = {a: [scores[b] for b, s in samples.items()
                    if s['case_id'] == case and s['arm'] == a and b in scores]
                for a in ('original', 'quality')}
        if any(len(v) != 3 for v in arms.values()):
            comparisons.append({'case_id': case, 'status': 'INCOMPLETE'})
            continue
        totals = {a: sum(sum(s.values()) for s in rows) for a, rows in arms.items()}
        target = rubric['targets'][case]
        target_sums = {a: sum(s[target] for s in rows) for a, rows in arms.items()}
        comparisons.append({'case_id': case, 'status': 'SCORED',
                            'mean_total': {a: v / 3 for a, v in totals.items()},
                            'target': target, 'target_sum': target_sums,
                            'noninferior': totals['quality'] >= totals['original'],
                            'target_improved': target_sums['quality'] > target_sums['original']})
    complete = not problems and len(scores) == 36
    passed = complete and not hazards and all(c.get('noninferior') for c in comparisons) and sum(c.get('target_improved', False) for c in comparisons) >= 4
    return {'status': 'PASS' if passed else 'FAIL' if complete else 'INCOMPLETE',
            'valid_review_count': len(scores), 'result_states': dict(states),
            'hazards': dict(hazards), 'problems': problems, 'cases': comparisons,
            'scope': 'Fixed-evidence quality only; open research and unique-entry E2E remain separate gates'}


def main():
    """Offline files only. Reviewer directory contains no manifest or group map."""
    import argparse
    from pathlib import Path
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('pack', 'summarize'))
    parser.add_argument('batch', type=Path)
    parser.add_argument('output', type=Path, help='New artifact directory; never overwrites a review')
    parser.add_argument('--pack', type=Path, help='Existing pack directory for summarization')
    parser.add_argument('--reviews', type=Path, help='Directory of independently completed review JSON files')
    args = parser.parse_args()
    def read(path): return json.loads(path.read_text(encoding='utf-8'))
    def write(path, value): path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')
    manifest, rubric = read(args.batch/'manifest.json'), read(args.batch/'rubric.json')
    validate_manifest(manifest, rubric)
    results = [read(p) for p in sorted(args.batch.glob('result-*.json'))]
    if args.action == 'pack':
        result_map = {r['sample']['blind_id']: r for r in results}
        if len(result_map) != len(results) or set(result_map) - {s['blind_id'] for s in manifest['samples']}:
            raise ValueError('Unknown or duplicate results')
        packets, bindings, defects = [], [], []
        for sample in manifest['samples']:
            result = result_map.get(sample['blind_id'])
            if not result or result['output'].get('run_status') != 'COMPLETED':
                defects.append({'blind_id': sample['blind_id'], 'status': result['output'].get('run_status') if result else 'MISSING'})
                continue
            case = read(args.batch/(sample['case_id']+'.json'))
            frozen = next(c for c in manifest['cases'] if c['id'] == sample['case_id'])
            if case['input_hash'] != frozen['input_hash']: raise ValueError('Frozen case differs')
            packet, binding = blind_packet(sample, result, case)
            packets.append(packet); bindings.append(binding)
        args.output.mkdir(parents=True, exist_ok=False)
        reviewer = args.output/'reviewer'; reviewer.mkdir()
        write(reviewer/'rubric.json', rubric)
        for packet in packets: write(reviewer/(packet['blind_id']+'.json'), packet)
        write(args.output/'bindings.json', bindings)
        write(args.output/'defects.json', defects)
        print(f'Packed {len(packets)} valid results; retained {len(defects)} incomplete/failed slots. No scores assigned.')
    else:
        if args.pack is None or args.reviews is None: parser.error('--pack and --reviews required')
        bindings = read(args.pack/'bindings.json')
        for binding in bindings:
            packet = read(args.pack/'reviewer'/(binding['blind_id']+'.json'))
            if digest(packet) != binding['packet_hash']: raise ValueError('Review packet changed')
        reviews = [read(p) for p in sorted(args.reviews.glob('*.json'))]
        report = summarize(manifest, rubric, results, bindings, reviews)
        args.output.mkdir(parents=True, exist_ok=False)
        write(args.output/'summary.json', report)
        print(report['status'], report['valid_review_count'], '/36 valid reviewed outputs')


if __name__ == '__main__':
    main()
