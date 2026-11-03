"""Compact author/reviewer projections without changing original role receipts."""
from harness.runtime.evidence import model_evidence


def author_material_view(session):
    if (getattr(session, 'record', None) or {}).get('research_contract_version') != 3:
        return {'evidence':model_evidence(session.evidence_index()),
                'artifacts':session.artifacts() if hasattr(session, 'artifacts') else {}}
    evidence = model_evidence(session.evidence_index())
    excerpts = {}
    for artifact in session.artifacts().values():
        if artifact.get('kind') != 'source_excerpt':
            continue
        excerpt = artifact['excerpt']
        excerpts.setdefault(excerpt['source_id'], set()).add(excerpt['quote'])
    for source_id, source in evidence['sources'].items():
        source['received'] = sorted(excerpts.get(source_id, set()))
        source['received_scope'] = 'VERIFIED_ROLE_EXCERPTS'
        source['full_receipt'] = '完整工具回执及原文沿原task_evidence回读，不在作者视图重复展开'
    artifacts = {}
    for ref, artifact in session.artifacts().items():
        kind = artifact.get('kind')
        if kind in ('source_excerpt', 'validated_calculation', 'figure', 'role_claim_checks'):
            artifacts[ref] = artifact
        else:
            artifacts[ref] = {key:value for key,value in artifact.items()
                              if key in ('kind', 'producer_role', 'sha256', 'result_ref',
                                         'validation_error', 'validation_errors', 'path',
                                         'content_sha256', 'tool', 'status', 'call_id',
                                         'result_sha256', 'result_status', 'source_id')}
    return {'evidence':evidence, 'artifacts':artifacts}


def role_material_view(session, batch_id):
    """One copy of usable evidence, calculations and analysis for dependent roles."""
    material = author_material_view(session)
    artifacts = {ref: value for ref, value in material['artifacts'].items()
                 if ref.startswith('role:' + batch_id + ':')}
    for ref, artifact in session.artifacts().items():
        if ref in artifacts and artifact.get('kind') == 'role_claims':
            artifacts[ref] = artifact
    return {'aliases':session.record.get('role_aliases', {}).get(batch_id, {}), 'artifacts':artifacts}
