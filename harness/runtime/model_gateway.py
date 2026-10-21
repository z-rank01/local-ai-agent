"""Conversation model gateway backed by the existing transports and ledger."""
from copy import deepcopy
from harness.contracts.models import ModelRequest


class ConversationModelGateway:
    def __init__(self, runtime, execution_profile):
        self._runtime = runtime
        self._profile = deepcopy(execution_profile)

    async def generate(self, request: ModelRequest) -> dict:
        from core.model_execution import invoke
        if not isinstance(request.prompt, str) or not request.prompt.strip():
            raise ValueError('模型请求须包含非空指令文本')
        profile, material = deepcopy((self._profile, request.material))
        return await invoke(self._runtime, profile, request.prompt, material, request_id=request.request_id)
