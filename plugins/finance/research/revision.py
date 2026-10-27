"""Explicit, read-only lineage for a new revision; no old task is resumed."""
from hashlib import sha256
from pathlib import PurePosixPath
import re

from .session import encoded


REFERENCE = re.compile(r'^/workspace/reports/chat-research/([a-f0-9]{32})/research\.json$')
METHOD = re.compile(r'^/workspace/\.skill_registry/versions/[a-f0-9]{64}/skills/'
                    r'harness-stock-research-v[1-4]/references/[A-Za-z0-9_-]+\.md$')


async def read_document(session, path):
    parts, offset, digest = [], 0, None
    while True:
        page = await session.router.dispatch('file_read', {'path': path, 'offset': offset}, session.conversation_id)
        if page.get('error') or not isinstance(page.get('content'), str):
            raise ValueError('修订所需文件未能读取：' + path)
        if digest is not None and page.get('sha256') != digest:
            raise ValueError('修订读取期间文件发生变化')
        digest = page.get('sha256')
        span = page.get('range') or {}
        if not span:
            if offset or page.get('next_offset') is not None:
                raise ValueError('修订文件缺少完整分页')
            return page['content'], digest
        start, end = span.get('start'), span.get('end')
        if start != offset or type(end) is not int or not offset < end <= offset + len(page['content']):
            raise ValueError('修订文件分页不连续')
        parts.append(page['content'][:end - start])
        following = page.get('next_offset')
        if following is None:
            if span.get('total', page.get('total_chars', end)) != end:
                raise ValueError('修订文件缺少尾页')
            return ''.join(parts), digest
        if following != end:
            raise ValueError('修订文件游标不连续')
        offset = following


async def previous_record(session, reference):
    match = REFERENCE.fullmatch(reference) if isinstance(reference, str) else None
    if match is None:
        raise ValueError('revision_of须为本工作区既有32位研究ID的research.json路径')
    directory = str(PurePosixPath(reference).parent)
    previous = await session.read_json(reference)
    if previous.get('id') != match.group(1) or previous.get('directory') != directory:
        raise ValueError('旧研究身份与目录不一致，不能建立修订')
    status = previous.get('status')
    if status not in ('PUBLISHED', 'DELIVERY_INCOMPLETE'):
        raise ValueError('只有已发布或已结束为交付未完成的研究可以建立关联修订')
    lineage = {'research_id': previous['id'], 'directory': directory,
               'previous_status': status, 'draft_inherited': False}
    registered = previous.get('draft', {})
    draft = registered.get('path') if isinstance(registered, dict) else None
    candidates = ([draft] if isinstance(draft, str) and PurePosixPath(draft).parent == PurePosixPath(directory) else [])
    candidates.append(directory + '/model-original.md')
    for path in candidates:
        try:
            content, digest = await read_document(session, path)
        except FileNotFoundError:
            continue
        if not content.strip() or not digest:
            raise ValueError('旧研究缺少可核验的原始草稿')
        lineage.update(original_draft_path=path, original_draft_sha256=digest, draft_inherited=True)
        break
    if status == 'PUBLISHED' and not lineage['draft_inherited']:
        raise ValueError('已发布研究缺少可核验的原始草稿')
    return previous, lineage


async def inherit(session, previous, lineage):
    directory = previous['directory']
    index = await session.read_json(directory + '/evidence-index.json')
    sources, pages, received, notes = {}, {}, {}, {}
    for key, source in previous.get('sources', {}).items():
        path = source.get('path')
        if (not re.fullmatch(r'S[a-f0-9]{16}', key) or not isinstance(path, str)
                or not re.fullmatch(r'/workspace/reports/chat-research/[a-f0-9]{32}/sources/' + key + r'\.json', path)):
            raise ValueError('旧来源标识或目录不符合研究契约')
        saved = await session.read_json(path)
        if sha256(encoded(saved).encode()).hexdigest() != source.get('artifact_sha256'):
            raise ValueError('旧来源文件指纹已变化，未继承：' + key)
        if saved.get('sha256') != source.get('sha256') or not isinstance(saved.get('pages'), list):
            raise ValueError('旧来源原件指纹或页面缺失：' + key)
        sources[key], pages[key] = source, saved['pages']
        saved_evidence = index.get('sources', {}).get(key, {})
        spans = saved_evidence.get('received', [])
        if not isinstance(spans, list) or any(not isinstance(text, str) for text in spans):
            raise ValueError('旧读取证据格式无效：' + key)
        text = '\n'.join(page.get('text', '') for page in saved['pages'])
        paged_text = '\n\n'.join(f"[第 {page['page']} 页]\n{page.get('text', '')}" for page in saved['pages'])
        saved_json = encoded(saved)
        for span in spans:
            value = re.sub(r'\n+\[已读到文件末尾：[^\n]*\]\s*$', '', span)
            if not any(candidate and candidate in source for candidate in (value, value.removeprefix('[第 1 页]\n'))
                       for source in (text, paged_text, saved_json)):
                raise ValueError('旧读取证据未绑定已保存原件：' + key)
        received[key], notes[key] = spans, saved_evidence.get('annotation', {})
    methods, method_text, fingerprints = set(), {}, {}
    for path in previous.get('methods', []):
        if not METHOD.fullmatch(path):
            continue
        content, digest = await read_document(session, path)
        if not content.strip() or not digest:
            raise ValueError('旧方法版本无法核验：' + path)
        methods.add(path)
        method_text[path], fingerprints[path] = content, digest
    excerpts = {}
    for key, rows in previous.get('excerpts', {}).items():
        for row in rows:
            page = next((item.get('text') for item in pages[key] if item.get('page') == row.get('page')), None)
            start, end = row.get('start'), row.get('end')
            if (page is None or type(start) is not int or type(end) is not int
                    or page[start:end] != row.get('quote') or row.get('sha256') != sources[key]['sha256']):
                raise ValueError('旧摘录未通过原件核验：' + key)
            excerpts.setdefault(key, []).append(row)
    session.sources, session._source_pages = sources, pages
    session.read_text, session.evidence_notes = received, notes
    session.methods, session.method_text, session.method_fingerprints = methods, method_text, fingerprints
    session.excerpts = excerpts
    for key, spans in received.items():
        for text in spans:
            session.progress('read:' + key, text)
    for path in methods:
        session.progress('method', path)
    session._observed_milestones = len(session._milestones)
    session.record['revision_of'] = lineage
