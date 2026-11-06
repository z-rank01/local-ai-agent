"""Compact author/reviewer projections without changing original role receipts."""
from harness.runtime.evidence import model_evidence


def publication_links(candidate, sources):
    """The exact source labels and destinations used by the report renderer."""
    links = {}
    for citation in candidate.get('sources', []):
        key = citation.get('id')
        if key in sources and sources[key].get('url'):
            links[key] = sources[key]['url']
    return links


def _verified_citation_range(citation, excerpts, source_sha):
    quote = citation.get('quote')
    if not isinstance(quote, str) or not quote or not source_sha:
        return None
    loc = citation.get('excerpt')
    matches = set()
    for excerpt in excerpts:
        if excerpt.get('sha256') != source_sha:
            continue
        page, start, end = (excerpt.get(k) for k in ('page', 'start', 'end'))
        text = excerpt.get('quote')
        if (not all(type(n) is int for n in (page, start, end)) or not isinstance(text, str)
                or end - start != len(text)):
            continue
        if loc is not None:
            if (isinstance(loc, dict) and all(type(loc.get(k)) is int for k in ('page', 'start', 'end'))
                    and (page, start, end) == (loc['page'], loc['start'], loc['end']) and quote == text):
                matches.add((page, start, end))
        else:
            # A citation may contain an exact subquote of a registered excerpt
            # without supplying offsets. Never choose between distinct matches.
            offset = text.find(quote)
            if offset >= 0:
                if text.rfind(quote) != offset:
                    return None
                matches.add((page, start + offset, start + offset + len(quote)))
    return next(iter(matches)) if len(matches) == 1 else None


def cited_page_context(session, candidate):
    """Show context for verified citations on pages already received in this session."""
    intervals = {}
    pages = {}
    for citation in candidate.get('sources', []):
        key = citation.get('id')
        if key not in session.sources:
            continue
        source_sha = session.sources[key].get('sha256')
        loc = _verified_citation_range(citation, session.excerpts.get(key, []), source_sha)
        if loc is None:
            continue
        page, quote_start, quote_end = loc
        page_key = key, page
        if page_key not in pages:
            body = next((p['text'] for p in session._source_pages.get(key, []) if p['page'] == page), '')
            pages[page_key] = body if body and any(body in span for span in session.read_text.get(key, [])) else ''
        body = pages[page_key]
        if not body or body[quote_start:quote_end] != citation['quote']:
            continue
        # A short disclosure page often gives the period for a dividend in the
        # paragraph preceding the cited amount. On long pages show only neighbors.
        start, end = ((0, len(body)) if len(body) <= 2048 else
                      (max(0, quote_start - 256), min(len(body), quote_end + 256)))
        intervals.setdefault(page_key, []).append((start, end))
    result = []
    for (key, page), spans in sorted(intervals.items()):
        merged = []
        for start, end in sorted(spans):
            if merged and start <= merged[-1][1]:
                merged[-1][1] = max(merged[-1][1], end)
            else:
                merged.append([start, end])
        for start, end in merged:
            result.append({'source_id':key, 'sha256':session.sources[key]['sha256'], 'page':page,
                           'start':start, 'end':end, 'text':pages[key,page][start:end]})
    return result


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
