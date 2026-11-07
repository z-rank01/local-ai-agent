"""Drive a conversation to its next request, terminal receipt, or cancellation."""
from contextlib import aclosing
from dataclasses import dataclass
from collections.abc import AsyncIterator, Callable


NEXT_ROUND = object()


@dataclass(frozen=True)
class TurnRounds:
    step: Callable[[], AsyncIterator]
    permitted: Callable[[], bool]
    exhausted: object


async def execute_turn(program):
    """Prepare the policy, drive its rounds, and return failures to its receipt handler."""
    async with aclosing(program):
        async for item in program:
            if not isinstance(item, TurnRounds):
                yield item
                continue
            try:
                async with aclosing(run_rounds(item.step, item.permitted, item.exhausted)) as events:
                    async for event in events:
                        yield event
            except Exception as exc:
                try:
                    first = await program.athrow(exc)
                except StopAsyncIteration:
                    return
                yield first


async def run_rounds(step, permitted, exhausted):
    while permitted():
        advance = False
        async with aclosing(step()) as events:
            async for event in events:
                if event is NEXT_ROUND:
                    advance = True
                else:
                    yield event
        if not advance:
            return
    yield exhausted
