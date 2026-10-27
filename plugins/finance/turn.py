"""Finance-owned source, purpose and delivery state for the generic agent loop."""
import os
from harness.runtime.turns import NullTurn
from .source_review import SourceReview
from .source_quotes import (READ_TOOLS, original_texts, original_records,
                            source_index_guidance)
from .evidence import EvidenceIndex
from .source_coverage import merge_read_records
from .guidance import compose_guidance
from .task_source_view import lookup_index
from .reply_purpose import selection_view, analysis_decision


DELIVERY_GUIDANCE = (
    '按本轮问题语义选择回复用途，保留模型自主补查：仅核对来源保存或读取范围时，'
    '可用现有原文读取工具的source_scope及对应URL；仅核对程序规模及算术时，'
    '可用报告读取工具的calculation。经营、趋势、比较、原文含义及混合问题用analysis。'
    '展示用途只适用于实际成功读取的合法回执；没有相应回执时不能声称已完成该用途。'
)


class FinanceTurn(NullTurn):
    def __init__(self, context=None):
        super().__init__()
        self.bridge = (context or {}).get('stock-bridge')
        self.research = None
        self.comparison = None
        self.window_seconds = int(os.environ.get('STOCK_WINDOW_SECONDS', '600'))
        self.source_review = SourceReview()
        self.quality_research = self.bridge is not None
        self.loop_enabled = self.bridge is not None
        self.reply_purpose_enabled = self.bridge is not None
        self.source_review_enabled = self.bridge is not None
        self.delivery_guidance = DELIVERY_GUIDANCE

    def stream(self, host, messages, session_id, conversation_key):
        super().stream(host, messages, session_id, conversation_key)
        from .loop import FinanceLoop
        if self.bridge is None:
            self.quality_research = bool(getattr(host, 'quality_first_research', False))
            self.loop_enabled = bool(getattr(host, 'turn_loop_enabled', False))
            self.reply_purpose_enabled = bool(getattr(host, 'turn_reply_purpose_enabled', False))
            self.source_review_enabled = bool(getattr(host, 'turn_source_review_enabled', False))
        host.quality_first_research = self.quality_research
        host.turn_loop_enabled = self.loop_enabled
        host.turn_source_review_enabled = self.source_review_enabled
        host.turn_reply_purpose_enabled = self.reply_purpose_enabled
        host.turn_window_seconds = getattr(host, 'turn_window_seconds', self.window_seconds)
        return FinanceLoop(host).run(messages, session_id, conversation_key)

    async def configure(self, method_id, answer_only):
        self.quality_research = self.bridge is not None and not answer_only
        self.reply_purpose_enabled = self.bridge is not None and not answer_only
        self.loop_enabled = self.bridge is not None and not answer_only
        return [] if answer_only else await self.extra_system_sections(method_id)

    def checkpoint_state(self, archive):
        if self.research is None:
            return None
        state = self.research.checkpoint_state(archive)
        if state is None:
            return None
        return {'state': state, 'evidence_ids': sorted({eid for spans in state['read_text'].values()
                                                        for eid in spans})}

    def restore_state(self, state, load):
        if state is not None:
            if self.research is None:
                raise ValueError('金融研究能力未启用，不继续旧研究')
            self.research.restore_state(state.get('state', state), load)

    async def verify_restore(self, read_file):
        if self.research is None:
            return
        for source in self.research.sources.values():
            page = await read_file({'path': source['path'], 'max_chars': 1})
            if page.get('sha256') != source.get('artifact_sha256'):
                raise ValueError('检查点来源文件缺失或已变化，未继续生成')
        for path, fingerprint in self.research.method_fingerprints.items():
            page = await read_file({'path': path, 'max_chars': 1})
            if page.get('sha256') != fingerprint:
                raise ValueError('已读取的方法文件已变化，未继续生成')

    async def extra_system_sections(self, method_id):
        if self.bridge is None:
            return []
        sections = []
        if method_id != 'auto':
            from .service import stock_service
            try:
                rows = (await stock_service.request('/api/control/methods'))['methods']
                selected = next((row for row in rows if row['id'] == method_id), None)
                if selected:
                    sections.append('本会话人工选择的研究方法：' + selected['title'] + '，版本 ' + selected['version']
                        + '。仅在当前问题适用时采用。方法帮助提问和核查，不能代替事实、来源或股票交易门禁。\n'
                        + selected['content'])
            except (ValueError, OSError) as exc:
                import httpx
                if not isinstance(exc, httpx.HTTPError):
                    raise
        seconds = self.window_seconds
        sections.append('股票工具调用：先判断用户问题尚缺哪些可核对的证据，只为具体缺口查找或读取。'
            '已有结果足够时直接回答；不要重复已成功的查找、读取或任务提交。'
            '根据已返回的总页数停止读取，不请求不存在的页；任务引用只作工具参数，不在回答中展示。'
            '参数错误可定向修正；结果未知不能自动重发。'
            + (f'旧股票工具通路交付窗口为{seconds}秒；' if seconds and seconds > 0 else '旧股票工具通路不设固定交付窗口；')
            + 'research_begin开启的聊天自主研究不受此交接窗口约束。设置交接窗口时，旧通路到时通过已有提交工具和真实受理回执，'
            '将尚未完成且已授权的公开研究交给股票工作进程自动继续，无需要求用户重复发送“继续”。'
            '保留原问题、反证、完成判据及已保存来源；已有后台任务直接查询，不重复提交。'
            '未受理、结果未知、材料不可得或没有进展时，交付已核对内容并说明实际缺口，不能声称后台已接续。'
            '后续追问依据本会话已保存的工具结果、来源和游标接续；必要时重新查找有效引用，不刷新简报或重建已受理任务。')
        return sections

    def before_tool(self, tool, arguments):
        if tool == 'web_fetch':
            return {**arguments, 'include_file': bool(self.research and self.research.active)}
        return arguments

    async def after_tool(self, tool, result):
        if tool != 'web_fetch' or not isinstance(result, dict):
            return result
        if self.research and self.research.active:
            return await self.research.save_source(result)
        result = dict(result)
        result.pop('_source_file', None)
        return result

    def start_turn(self, conversation_id, request_id, question, method_id, execution_profile, allow_report_body):
        if self.bridge is not None:
            self.bridge.set_turn(conversation_id, request_id, question, method_id=method_id,
                execution_profile=execution_profile, allow_report_body=allow_report_body)
        from .brief_comparison import BriefComparison, requested
        self.comparison = BriefComparison() if requested(question) else None

    async def finish_turn(self, conversation_id):
        return await self.bridge.finish_turn(conversation_id) if self.bridge is not None else []

    def comparison_tools(self):
        return {'stock_market_brief_find', 'stock_report_read'}

    def allowed_tools(self):
        discovery = super().allowed_tools()
        if discovery is not None:
            return discovery
        return self.comparison_tools() if self.comparison is not None else None

    def comparison_accept(self, data):
        if self.comparison is not None:
            self.comparison.accept(data)

    def comparison_missing_required_reads(self):
        return self.comparison.missing_required_reads() if self.comparison is not None else []

    @property
    def buffer_output(self):
        return self.comparison is not None

    def accept_tool(self, data):
        self.comparison_accept(data)

    def checkpoint_event(self, data):
        result = data.get('result')
        if not isinstance(result, dict):
            return None
        if isinstance(result.get('draft'), dict):
            return {'draft': result['draft']}
        if data.get('status') == 'ok' and result.get('status') == 'PUBLISHED':
            return {'published': {key: result[key] for key in ('path', 'title', 'sha256')}}
        return None

    def failure_text(self, text, error):
        if self.comparison is not None:
            return '版本对比未完成；请查看已打开的本地附件并稍后重试。'
        return text + ('\n\n' if text else '') + '本轮未完成：' + error

    def interrupted_text(self, text):
        return '版本对比已中断；已取得的附件仍可查看。' if self.comparison is not None else text + '\n\n[生成已中断]'

    async def completion_events(self, conversation_id):
        if self.comparison is None:
            return
        import json
        import uuid
        from core.agent import AgentEvent
        for reference, page in self.comparison_missing_required_reads():
            call_id = uuid.uuid4().hex
            name = self.artifact_tool_name()
            params = {'reference': reference, 'page': page}
            yield AgentEvent('tool_start', data={'name': name, 'call_id': call_id,
                'params': params, 'purpose': '核对简报版本'})
            result = await self.read_artifact(reference, page, conversation_id)
            status = 'error' if result.get('error') else 'ok'
            yield AgentEvent('tool_end', text='版本读取已核对' if status == 'ok' else '版本读取未完成',
                data={'name': name, 'call_id': call_id, 'params': params, 'status': status,
                      'result': result, 'purpose': '核对简报版本',
                      'result_preview': json.dumps(result, ensure_ascii=False, default=str)})
            if status == 'error':
                return
        yield AgentEvent('done', text=self.comparison_render(), data={'final': True})

    def comparison_render(self):
        return self.comparison.render() if self.comparison is not None else ''

    async def read_artifact(self, reference, page, conversation_id):
        if self.bridge is None:
            raise RuntimeError('金融成果服务未接入')
        return await self.bridge.call_tool('stock_report_read', {'reference': reference, 'page': page}, conversation_id)

    def artifact_tool_name(self):
        return 'stock_report_read'

    def has_open_turn(self, conversation_id):
        return self.bridge is not None and self.bridge.has_open_turn(conversation_id)

    def start(self, history):
        self.source_quote_originals = original_texts(history)
        self.source_read_records = original_records(history)
        self.evidence_index = EvidenceIndex()
        self.evidence_index.update(history)
        self.source_index = self.evidence_index.sources
        self.source_calculation_index = dict(self.evidence_index.calculations)
        self.source_comparison_index = dict(self.evidence_index.comparisons)

    def update_sources(self, messages, merge_records=False):
        self.evidence_index.update(messages)
        updated = self.evidence_index.sources
        tokens = {row['source_reference'] for row in updated}
        self.source_index = updated + [row for row in self.source_index
                                      if row['source_reference'] not in tokens]
        if merge_records:
            self.source_read_records = merge_read_records(self.source_read_records, original_records(messages))
        self.source_calculation_index.update(self.evidence_index.calculations)
        self.source_comparison_index.update(self.evidence_index.comparisons)

    def compose_guidance(self, **kwargs):
        result = compose_guidance(**kwargs)
        self.current_calculations = result.calculations
        self.cashflow_scope_version = result.cashflow_scope_version
        self.has_comparison = result.has_comparison
        self.has_coverage = result.has_coverage
        return result

    def lookup_index(self, index, question):
        return lookup_index(index, question)

    def source_index_guidance(self, rows):
        return source_index_guidance(rows)

    def purpose_view(self, **kwargs):
        return selection_view(**kwargs)

    def analysis_decision(self, content):
        return analysis_decision(content)

    def source_tool_for_phase(self, phase):
        return 'stock_public_evidence_' + phase

    def source_parameter_tools(self):
        return ('stock_public_evidence_find', 'stock_public_evidence_read', 'context_evidence_read')

    def review_source_tools(self):
        return frozenset(READ_TOOLS)

    def may_start_literal_quote(self, text):
        from .source_quotes import may_start_literal_quote
        return may_start_literal_quote(text)

    def original_texts(self, messages):
        return original_texts(messages)

    def original_records(self, messages):
        return original_records(messages)

    def complete_sources(self, records):
        from .source_quotes import complete_sources
        return complete_sources(records)

    def incomplete_delivery(self, *args, **kwargs):
        from .source_review import incomplete_delivery
        return incomplete_delivery(*args, **kwargs)

    def review_reply(self, *, accumulated, messages, session_id, record, review_visible_prefix,
                     pending_tokens, active, window_closed, no_progress_closed):
        from harness.contracts.turn_policy import TurnReview
        from .source_quotes import (original_texts, original_records, complete_sources,
            unverified_quotes, unverified_absence_claims, quote_feedback, isolate_quote_paragraphs,
            repair_quote_format, _review_payload)
        from .calculation_review import repair_scale_divisions, repair_scale_units
        reply = accumulated.get('content') or ''
        events = []
        declared_review = _review_payload(reply)
        if declared_review is not None:
            record('source_review_role_applied', {'session_id': session_id,
                'version': 'declared-review-role-v1', 'decision_count': len(declared_review['decisions']),
                'semantic_certification': False})
        self.source_quote_originals = list(dict.fromkeys(self.source_quote_originals + original_texts(messages)))
        self.source_read_records = merge_read_records(self.source_read_records, original_records(messages))
        calculated_reply, calculation_edits = repair_scale_divisions(reply, self.current_calculations)
        calculated_reply, unit_edits = repair_scale_units(calculated_reply, self.current_calculations)
        calculation_edits += unit_edits
        if calculation_edits:
            events.append(('quality_repaired', {'original_answer': reply,
                'quality_edits': calculation_edits, 'quality_status': 'repaired_program_scale'}))
            emitted_chars = len(review_visible_prefix) - sum(len(token) for token in pending_tokens)
            if calculated_reply[:emitted_chars] != reply[:emitted_chars]:
                events.append(('quality_rejected', {'quality_status': 'replaced_program_scale_draft',
                    'message': {'role': 'system', 'content': '已用同版程序结果局部纠正规模比值，原错误文字仅留审计。'},
                    'rejected_answer': reply, 'quality_issues': calculation_edits}))
                emitted_chars = 0
            reply = calculated_reply
            accumulated = {**accumulated, 'content': reply}
            messages[-1] = accumulated
            pending_tokens = [reply[emitted_chars:]]
            record('program_scale_repaired', {'session_id': session_id,
                'count': len(calculation_edits), 'provider_call_added': False})
        repaired, format_edits = repair_quote_format(reply, self.source_quote_originals)
        if format_edits:
            events.append(('quality_repaired', {'original_answer': reply,
                'quality_edits': format_edits, 'quality_status': 'repaired_literal_format'}))
            emitted_chars = len(review_visible_prefix) - sum(len(token) for token in pending_tokens)
            reply = repaired
            accumulated = {**accumulated, 'content': reply}
            messages[-1] = accumulated
            pending_tokens = [reply[emitted_chars:]]
            record('source_quote_format_repaired', {'session_id': session_id,
                'count': len(format_edits), 'provider_call_added': False})
        issues = unverified_quotes(reply, self.source_quote_originals)
        issues += unverified_absence_claims(reply, complete_sources(self.source_read_records))
        retry = False
        if issues:
            feedback = quote_feedback(issues)
            record('source_quote_rejected', {'session_id': session_id,
                'count': len(issues), 'repair_attempt': self.source_review.attempts})
            events.append(('quality_rejected', {'message': {'role': 'system', 'content': feedback},
                'rejected_answer': reply, 'quality_issues': issues}))
            if self.source_review.attempts < 2 and active and not window_closed and not no_progress_closed:
                self.source_review.begin(feedback)
                messages.append({'role': 'system', 'content': feedback})
                retry = True
            else:
                accumulated = {**accumulated, 'content': isolate_quote_paragraphs(reply, issues)}
                messages[-1] = accumulated
                pending_tokens = [accumulated['content']]
        protocol_events = []
        for kind, payload in events:
            metadata = ({'cloud_safe': False, 'message': {'role': 'system', 'content':
                '已按本轮授权原文或同版程序结果作确定的局部修复；其他事实和缺失判断仍须核验。'},
                'quality_status': payload['quality_status'], 'original_answer': payload['original_answer'],
                'quality_edits': payload['quality_edits']} if kind == 'quality_repaired' else
                {'message': payload['message'], 'quality_status': payload.get('quality_status', 'rejected_source_quote'),
                 'rejected_answer': payload['rejected_answer'], 'quality_issues': payload['quality_issues']})
            protocol_events.append((kind, {**payload, 'protocol': metadata,
                'reset_output': kind == 'quality_rejected'}))
        return TurnReview(action='continue' if retry else 'none', message=accumulated,
                          pending_tokens=pending_tokens, events=protocol_events,
                          pending_text='' if issues else None)
