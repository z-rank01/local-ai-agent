"""Lossless, capability-sized source windows for independent long-material review.

This prepares evidence, never scores it. A plan is not evidence that a model read
the windows: all node receipts and a final review are still required.
"""
import hashlib
import json
import math

from .quality_review import digest


def encode(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, indent=1, allow_nan=False)


def estimate(text):
    # Same conservative character estimator as the application, rounded upward.
    return math.ceil(len(text) / 2.5 * 1.33)


def source_hash(text):
    return hashlib.sha256(text.encode('utf-8')).hexdigest()


def plan_windows(packet, *, context_window, output_reserve, system_reserve, instruction):
    """Include the whole answer/question in each independent source read.

    Contiguous serialized source spans have exact offsets and hashes. Newlines
    are preferred boundaries; an oversized single value is split, not dropped.
    No source content is treated as an instruction or promoted to a fact.
    """
    if any(type(n) is not int or n < 0 for n in (context_window, output_reserve, system_reserve)):
        raise ValueError('Explicit nonnegative capability and reserves required')
    if not instruction.strip():
        raise ValueError('Reading instruction required')
    text = encode(packet['material'])
    common = {'blind_id': packet['blind_id'], 'research_context': packet['research_context'],
              'question': packet['material'].get('question'), 'answer': packet['answer'],
              'instruction': instruction, 'source_length': len(text), 'source_hash': source_hash(text)}
    available = context_window - output_reserve - system_reserve
    if available <= estimate(encode(common)):
        raise ValueError('Answer and review context require hierarchical preparation; nothing was truncated')
    windows = []
    start = 0
    while start < len(text):
        # Count the actual escaped request representation, not just raw source.
        low, high = start + 1, len(text)
        end = start
        while low <= high:
            middle = (low + high) // 2
            row = {**common, 'start': start, 'end': middle, 'source': text[start:middle]}
            if estimate(encode(row)) <= available:
                end = middle
                low = middle + 1
            else:
                high = middle - 1
        if end == start:
            raise ValueError('No capacity for a source span; no partial plan accepted')
        if end < len(text):
            newline = text.rfind('\n', start, end)
            if newline >= start:
                end = newline + 1
        row = {**common, 'start': start, 'end': end, 'source': text[start:end]}
        windows.append({'request': row, 'request_hash': digest(row),
                        'estimated_input_tokens': estimate(encode(row))})
        start = end
    plan = {'version': 1, 'packet_hash': digest(packet), 'context_window': context_window,
            'output_reserve': output_reserve, 'system_reserve': system_reserve,
            'source_hash': source_hash(text), 'source_length': len(text), 'windows': windows,
            'status': 'PLANNED_NOT_MODEL_READ'}
    verify_plan(plan, packet)
    return plan


def verify_plan(plan, packet):
    if plan['packet_hash'] != digest(packet):
        raise ValueError('Original review packet changed')
    text = encode(packet['material'])
    if plan['source_hash'] != source_hash(text) or plan['source_length'] != len(text):
        raise ValueError('Original source changed')
    cursor = 0
    for node in plan['windows']:
        request = node['request']
        if (request['start'] != cursor or request['end'] <= cursor
                or request['source'] != text[cursor:request['end']]
                or request['answer'] != packet['answer']
                or request['research_context'] != packet['research_context']
                or request['source_hash'] != plan['source_hash']
                or node['request_hash'] != digest(request)):
            raise ValueError('Source gap, changed answer or changed node')
        tokens = estimate(encode(request))
        if (tokens != node['estimated_input_tokens'] or
                tokens + plan['output_reserve'] + plan['system_reserve'] > plan['context_window']):
            raise ValueError('Node exceeds declared capacity')
        cursor = request['end']
    if cursor != len(text):
        raise ValueError('Source tail missing; incomplete coverage')
    return True


FINDING_GROUPS = ('supported_claims', 'unsupported_claims', 'counterevidence',
                  'conflicts', 'unresolved_questions')


def validate_reading(node, result):
    """A source-reading result cannot assign a final score or invent a quote.

    Semantic correctness remains a quality gate; this verifies exact provenance
    and that a partial source read is not represented as a completed review.
    """
    if set(result) != {'request_hash', 'findings', 'coverage_note'}:
        raise ValueError('Source reader must return findings, not a final score')
    if result['request_hash'] != node['request_hash']:
        raise ValueError('Reading belongs to another source window')
    if set(result['findings']) != set(FINDING_GROUPS) or not result['coverage_note'].strip():
        raise ValueError('All finding groups and an explicit coverage note required')
    request = node['request']
    for group, findings in result['findings'].items():
        if not isinstance(findings, list):
            raise ValueError('Findings must be lists')
        for finding in findings:
            if (set(finding) != {'claim', 'reason', 'source_spans'} or
                    not isinstance(finding['claim'], str) or not finding['claim'].strip() or
                    not isinstance(finding['reason'], str) or not finding['reason'].strip() or
                    not isinstance(finding['source_spans'], list)):
                raise ValueError('Claim, rationale and source spans required')
            if group in ('supported_claims', 'counterevidence', 'conflicts') and not finding['source_spans']:
                raise ValueError('Positive evidence requires exact original spans')
            for span in finding['source_spans']:
                if (set(span) != {'start', 'end', 'quote'} or
                        type(span['start']) is not int or type(span['end']) is not int or
                        not request['start'] <= span['start'] < span['end'] <= request['end']):
                    raise ValueError('Citation outside this source window')
                excerpt = request['source'][span['start']-request['start']:span['end']-request['start']]
                if span['quote'] != excerpt:
                    raise ValueError('Quoted evidence differs from immutable original')
    return result


def collect_readings(plan, packet, receipts):
    """Only complete, provenance-checked nodes may feed a final synthesis.

    Unknown/failed nodes are returned as gaps, never treated as empty evidence.
    This function neither retries calls nor assigns any quality score.
    """
    verify_plan(plan, packet)
    expected = {n['request_hash']: n for n in plan['windows']}
    seen, readings, gaps = set(), [], []
    for receipt in receipts:
        key = receipt['request_hash']
        if key not in expected or key in seen:
            raise ValueError('Unknown or duplicate source-reading receipt')
        seen.add(key)
        if receipt['status'] != 'SUCCEEDED':
            gaps.append({'request_hash': key, 'status': receipt['status']})
            continue
        provider_complete = receipt.get('response_id') and receipt.get('finish_reason') == 'stop'
        bff_complete = (receipt.get('receipt_kind') == 'BFF_SESSION_COMPLETED'
                        and receipt.get('message_id') and receipt.get('session_completed') is True
                        and receipt.get('errors') == [])
        if not (provider_complete or bff_complete):
            gaps.append({'request_hash': key, 'status': 'UNCONFIRMED_OR_TRUNCATED'})
            continue
        readings.append(validate_reading(expected[key], receipt['result']))
    gaps.extend({'request_hash': key, 'status': 'MISSING'} for key in expected.keys()-seen)
    return {'status': 'READY_FOR_SYNTHESIS' if not gaps else 'INCOMPLETE_READING',
            'packet_hash': plan['packet_hash'], 'readings': readings, 'gaps': gaps,
            'final_review_complete': False}
