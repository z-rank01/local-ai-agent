"""One chat turn's research artifacts, using the existing workspace tools.

This is not a research scheduler or semantic judge. The model chooses methods,
sources and conclusions; this module records actual reads and publishes files.
"""
from datetime import datetime, timezone
from hashlib import sha256
import json
import logging
import re
from uuid import uuid4
import httpx

from .brief_document import render_document
from .research_visuals import render_charts, validate_publication

PHASES = ('准备', '获取', '分析', '核对', '交付')
RESEARCH_TOOLS = frozenset({
    'research_begin', 'brief_publish', 'skill_list', 'skill_info', 'file_read',
    'file_write', 'file_list', 'code_exec', 'shell_exec', 'web_search', 'web_fetch',
    'context_evidence_read', 'stock_report_read', 'stock_public_evidence_find',
    'stock_public_evidence_read', 'stock_research_find', 'stock_theme_research_find'})


def encoded(value):
    return json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False)


class ResearchSession:
    def __init__(self, router, *, conversation_id, model, allowed):
        self.router, self.conversation_id, self.model = router, conversation_id, model
        self.allowed = allowed
        self.record = None
        self.sources = {}
        self.read_text = {}
        self.trace = []
        self.methods = set()
        self.published = None
        self.draft_count = 0

    @property
    def active(self):
        return self.record is not None and self.published is None

    async def write(self, name, value, *, binary=False):
        result = await self.router.dispatch('file_write', {
            'path': self.record['directory'] + '/' + name,
            'content': value if isinstance(value, str) else encoded(value),
            'encoding': 'base64' if binary else 'utf-8'}, self.conversation_id)
        if not isinstance(result, dict) or not result.get('written'):
            raise RuntimeError('研究文件未保存成功')

    async def checkpoint(self):
        await self.write('research.json', {**self.record, 'sources': self.sources,
                                          'methods': sorted(self.methods), 'trace': self.trace})

    async def incomplete(self):
        if self.record is not None and self.published is None:
            self.record['status'] = 'DELIVERY_INCOMPLETE'
            await self.checkpoint()

    async def close(self):
        if self.record is None or self.record['status'] != 'IN_PROGRESS':
            return
        try:
            await self.incomplete()
        except (OSError, RuntimeError, httpx.HTTPError) as exc:
            # Preserve the original stop/failure; do not hide a failed checkpoint.
            logging.getLogger(__name__).error('Research terminal checkpoint failed: %s', type(exc).__name__)

    async def save_draft(self, content):
        # Visible draft only. Existing model reasoning is never copied to artifacts.
        content = re.sub(r'<think>.*?(?:</think>|$)', '', content, flags=re.S | re.I)
        if content.strip():
            self.draft_count += 1
            await self.write(f'drafts/{self.draft_count}.md', content)

    async def begin(self, params):
        if not self.allowed:
            raise PermissionError('研究保存与发布需要当前工作区读取和写入授权')
        phase = params.get('phase', '准备')
        if phase not in PHASES or phase == '交付':
            raise ValueError('交付阶段由 brief_publish 成功保存后设置')
        if self.record is None:
            if phase != '准备' or not params.get('scope') or not params.get('plan'):
                raise ValueError('首次研究需提供范围和简短计划，并从准备阶段开始')
            identifier = uuid4().hex
            directory = '/workspace/reports/chat-research/' + identifier
            # The executor owns new research directories; the file service may
            # save evidence there without making the parent unwritable to scripts.
            created = await self.router.dispatch('shell_exec', {
                'command': 'mkdir -p ' + directory}, self.conversation_id)
            if created.get('exit_code') != 0 or created.get('error'):
                raise RuntimeError('计算容器未能创建研究目录，请检查工作区执行权限')
            self.record = {'id': identifier, 'conversation_id': self.conversation_id,
                'model': self.model, 'started_at': datetime.now(timezone.utc).isoformat(),
                'scope': params['scope'], 'targets': params.get('targets', []),
                'plan': params['plan'], 'phase': phase, 'status': 'IN_PROGRESS',
                'directory': directory}
        else:
            self.record['phase'] = phase
        await self.checkpoint()
        return {'research_id': self.record['id'], 'directory': self.record['directory'],
                'phase': phase, 'model': self.model,
                'next': '按需读取 harness-stock-research-v1 方法；研究不会因十分钟自动转交专业任务。'}

    async def save_source(self, result):
        payload = result.pop('_source_file', None)
        if payload is None or not self.active:
            return result
        key = 'S' + sha256((result['url'] + result['sha256']).encode()).hexdigest()[:16]
        if key not in self.sources:
            extension = 'pdf' if result['media_type'] == 'application/pdf' else 'html'
            await self.write(f'sources/{key}.{extension}', payload['base64'], binary=True)
            await self.write(f'sources/{key}.json', {**result, 'pages': payload['pages']})
            self.sources[key] = {k: result[k] for k in
                ('url', 'title', 'sha256', 'fetched_at', 'page_count', 'total_chars', 'media_type')}
            self.sources[key]['path'] = self.record['directory'] + f'/sources/{key}.json'
        self.read_text.setdefault(key, []).append(result['content'])
        result['source_id'] = key
        result['saved_source'] = self.sources[key]['path']
        await self.checkpoint()
        return result

    async def observe(self, event):
        if self.record is None:
            return
        name, params, result = event['name'], event.get('params', {}), event.get('result', {})
        success = event.get('status') == 'ok'
        path = params.get('path', '')
        if success and name == 'file_read' and '/skills/harness-stock-research-v1/references/' in path:
            self.methods.add(path)
        if success and name == 'file_read' and isinstance(result, dict):
            for key, source in self.sources.items():
                if path == source['path']:
                    self.read_text.setdefault(key, []).append(result.get('content', ''))
        # Only fixed diagnostics, no code, model chain-of-thought or full arguments.
        self.trace.append({'tool': name, 'status': event['status'],
            'phase': self.record['phase'], 'elapsed': round(event.get('elapsed', 0), 3),
            'path': path if path.startswith('/workspace/') else None,
            'source_id': result.get('source_id') if isinstance(result, dict) else None,
            'at': datetime.now(timezone.utc).isoformat()})
        await self.checkpoint()

    async def publish(self, params):
        if not self.allowed or self.record is None:
            raise PermissionError('请先通过 research_begin 建立有权限的本轮研究')
        digest = sha256(encoded(params).encode()).hexdigest()
        if self.published:
            if self.published['sha256'] != digest:
                raise ValueError('本轮已经交付；不同内容须作为新的修订研究保存')
            return self.published
        if self.record['phase'] != '核对' or not self.methods or not self.sources:
            raise ValueError('发布前须实际读取方法、新公开原文，并进入核对阶段')
        validate_publication(params, self.sources, self.read_text)
        charts = render_charts(params.get('charts', []))
        references = '\n\n## 论证与参考\n'
        for item in params['sources']:
            source = self.sources[item['id']]
            references += f"\n- [{item['id']}]({source['url']})：{item['locator']}。原文：{item['quote']}\n"
        for calc in params.get('calculations', []):
            references += '\n- 计算：' + json.dumps(calc, ensure_ascii=False) + '\n'
        references += '\n资料限制：' + params['review']['limitations']
        original = params['markdown']
        document = render_document(original + references, title=params['title'],
            generated_at=datetime.now(timezone.utc).isoformat(), model=self.model,
            material_sha256=digest, as_of=params['as_of'], stance=params['stance'],
            scope_label=self.record['scope'], charts_html=charts,
            provenance='聊天自主研究 · 模型生成',
            material_note='本轮使用公开来源；资料时点见各来源。模型承担分析与自查，程序核对引用和算式不等于事实认证。')
        # HTML is the last publication file. Failure never returns a report card.
        await self.write('model-original.md', original)
        await self.write('report.md', original + references)
        await self.write('evidence.json', {**params, 'actual_sources': self.sources, 'model': self.model})
        await self.write('report.html', document)
        self.record.update(phase='交付', status='PUBLISHED')
        await self.checkpoint()
        self.published = {'status': 'PUBLISHED', 'sha256': digest,
            'path': self.record['directory'] + '/report.html', 'title': params['title'],
            'message': '模型生成简报已保存；请在回复中使用此工作区路径的 Markdown 链接展示报告卡。'}
        return self.published
