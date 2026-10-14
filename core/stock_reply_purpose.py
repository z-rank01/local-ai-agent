"""Semantic purpose selection, separate from the full research answer view.

Explicit receipt-bound references locate material, never classify intent or
grant permissions. The original history and model tool arguments stay intact.
"""
import json

VERSION = 'current-question-purpose-v1'
READ_TOOLS = {'stock_report_read', 'stock_public_evidence_find',
              'stock_public_evidence_read', 'context_evidence_read'}


def selection_view(history, question, lookup, definitions):
    if lookup['selection'] not in ('explicit_known', 'single_report_receipt') or not lookup['rows']:
        return None
    tools = [d for d in definitions if d['function']['name'] in READ_TOOLS]
    if not any(d['function']['name'] == 'stock_report_read' for d in tools):
        return None
    from .stock_source_quotes import source_index_guidance
    return ([history[0], {'role': 'system', 'content':
        '本步骤仅按当前用户问题的语义选择回复用途与必要的只读参数，不生成答案。'
        '经营、趋势、比较、原文含义及混合问题保留analysis；可以仅返回严格JSON '
        '{"reply_focus":"analysis"}，随后在完整授权历史中分析与补查。'
        '纯保存/收到范围核对使用现有stock_public_evidence_read，选择reply_focus=source_scope并填写实际URL。'
        '纯已保存公告规模算术使用现有stock_report_read选择reply_focus=calculation。'
        '这两个纯呈现用途需要本次实际读取来核对快照/展示版本，不能用旧答复或目录替代。'
        '不要求所有问题读源，不以关键词分类；由你选择。'
        '原用户要求、完整来源、反证、方法与历史没有删除，分析阶段仍完整提供，已有登记证据可以回读。'
        '以下只是当前任务的成功定位索引，不是完整材料清单或缺失证明。'
        '不编造或替换引用、事件、游标，不创建/恢复研究，不改变权限或模型。\n'
        +source_index_guidance(lookup['rows'])+lookup['note']},
        {'role': 'user', 'content': question}], tools)


def analysis_decision(content):
    try:
        return json.loads(content) == {'reply_focus': 'analysis'}
    except (ValueError, TypeError):
        return False
