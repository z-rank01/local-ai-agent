"""Finance research tools bound to the host-owned per-run research session."""


class ResearchTransport:
    def __init__(self, session=None):
        self.session = session

    def bind(self, context):
        return ResearchTransport(context.research)

    async def call_tool(self, tool, arguments, session_id):
        if self.session is None:
            raise RuntimeError('研究工具需要当前会话和工作区上下文')
        if tool == 'research_begin':
            return await self.session.begin(arguments)
        if tool == 'brief_publish':
            return await self.session.publish(arguments)
        if tool == 'brief_review':
            raise RuntimeError('单独核对由当前聊天运行处理，不在工具后端另起模型调用。')
        raise ValueError('未登记的研究工具')
