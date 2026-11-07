"""Default no-op turn policy for conversations without a domain orchestrator."""
class NullTurn:
    def subagent_extension(self):
        from .subagents.extensions import NullSubagentExtension
        return NullSubagentExtension()

    loop_enabled = False

    def start(self, history):
        pass

    def stream(self, host, messages, session_id, conversation_key):
        return None

    def execution_scope(self):
        return None

    async def configure(self, method_id, answer_only):
        return []

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
        # A discovery catalog describes methods, not tool authority. The router
        # still enforces workspace grants, installed providers and call policies.
        return None

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
