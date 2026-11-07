"""Stream one model call under its parent run's checkpoint and cancellation boundary."""
from contextlib import aclosing


async def model_step(source, messages, control):
    if control:
        await control.boundary(messages)
        control.begin_step('模型请求')
    complete = False
    async with aclosing(source) as stream:
        async for token, message in stream:
            if message:
                complete = True
            yield token, message
    if not complete:
        raise RuntimeError('模型未返回完整消息，已取得的结果仍保留。')
    if control:
        control.end_step()
