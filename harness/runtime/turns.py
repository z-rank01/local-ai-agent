"""Default no-op turn policy for conversations without a domain orchestrator."""
from harness.contracts.turn_policy import TurnGuidance


class NullSourceReview:
    attempts = 0
    parameter_step = False
    feedback = ''

    def received(self, event):
        pass

    def begin(self, feedback):
        pass

    def explain(self):
        pass


class NullTurn:
    def __init__(self):
        self.quality_research = False
        self.reply_purpose_enabled = False
        self.source_review_enabled = False
        self.loop_enabled = False
        self.window_seconds = None
        self.source_read_seen = False
        self.source_review = NullSourceReview()
        self.source_quote_originals = []
        self.source_read_records = []
        self.source_calculation_index = {}
        self.source_comparison_index = {}
        self.source_index = []
        self.delivery_guidance = ''
        self.discovery = None

    def start(self, history):
        pass

    def stream(self, host, messages, session_id, conversation_key):
        self.discovery = getattr(host.router, 'skill_session', None)
        return None

    async def configure(self, method_id, answer_only):
        return []

    def update_sources(self, messages, merge_records=False):
        pass

    def compose_guidance(self, **kwargs):
        return TurnGuidance()

    def lookup_index(self, index, question):
        return {'rows': [], 'selection': 'not_applicable', 'note': '', 'version': 'default-turn-v1'}

    def source_index_guidance(self, rows):
        return ''

    def purpose_view(self, **kwargs):
        return None

    def analysis_decision(self, content):
        return False

    def source_tool_for_phase(self, phase):
        return None

    def source_parameter_tools(self):
        return ()

    def review_source_tools(self):
        return frozenset()

    def may_start_literal_quote(self, text):
        return False

    def original_texts(self, messages):
        return []

    def original_records(self, messages):
        return []

    def complete_sources(self, records):
        return []

    def incomplete_delivery(self, *args, **kwargs):
        return '本轮未完成；已取得的资料和未核验结果保留。'

    def review_reply(self, **kwargs):
        return None

    def checkpoint_state(self, archive):
        return None

    def restore_state(self, state, load):
        if state is not None:
            raise ValueError('当前能力不兼容已保存的回合状态')

    async def verify_restore(self, read_file):
        pass

    async def finish_turn(self, conversation_id):
        return []

    async def completion_events(self, conversation_id):
        return
        yield

    def accept_tool(self, data):
        pass

    def allowed_tools(self):
        if self.discovery is None or not self.discovery.requires_discovery:
            return None
        names = ['skill_list', 'skill_info', 'file_list']
        for capability in self.discovery.snapshot.get('capabilities', []):
            names.extend(capability.get('tools', []))
        return tuple(dict.fromkeys(names))

    @property
    def buffer_output(self):
        return False

    def checkpoint_event(self, data):
        return None

    def failure_text(self, text, error):
        return text + ('\n\n' if text else '') + '本轮未完成：' + error

    def interrupted_text(self, text):
        return text + '\n\n[生成已中断]'

    def has_open_turn(self, conversation_id):
        return False

    def before_tool(self, tool, arguments):
        return arguments

    async def after_tool(self, tool, result):
        return result

    async def extra_system_sections(self, method_id):
        return []
