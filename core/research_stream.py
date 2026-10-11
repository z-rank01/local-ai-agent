"""Accumulate public completion content and terminal metadata, never hidden reasoning."""
import json
import httpx


class ResearchStreamError(ValueError):
    """Fixed diagnostic category; never includes source content or provider text."""
    def __init__(self, category):
        self.category = category
        super().__init__(category)


async def read_completion(response, progress):
    content, identity, model, reason, usage = [], None, None, None, {}
    lines = response.aiter_lines().__aiter__()
    while True:
        try:
            line = await anext(lines)
        except StopAsyncIteration:
            break
        except httpx.HTTPError:
            if reason in ('stop', 'length'):
                break  # Complete terminal content is known even if the optional usage chunk is lost.
            raise
        if not line.startswith('data:'):
            continue
        raw = line[5:].strip()
        if raw == '[DONE]':
            break
        try:
            event = json.loads(raw)
        except ValueError as exc:
            raise ResearchStreamError('STREAM_INVALID_JSON') from exc
        if not isinstance(event, dict):
            raise ResearchStreamError('STREAM_INVALID_STRUCTURE')
        if event.get('error'):
            raise ResearchStreamError('STREAM_PROVIDER_ERROR')
        current_id, current_model = event.get('id'), event.get('model')
        if (identity and current_id and identity != current_id) or (model and current_model and model != current_model):
            raise ResearchStreamError('STREAM_IDENTITY_MISMATCH')
        previous = (identity, model)
        identity, model = current_id or identity, current_model or model
        if (identity, model) != previous:
            progress({'response_id': identity, 'response_model': model})
        if isinstance(event.get('usage'), dict):
            usage = event['usage']
        choices = event.get('choices') or []
        if not isinstance(choices, list) or any(not isinstance(choice, dict) for choice in choices):
            raise ResearchStreamError('STREAM_INVALID_STRUCTURE')
        if len(choices) > 1:
            raise ResearchStreamError('STREAM_MULTIPLE_COMPLETIONS')
        for choice in choices:
            if choice.get('index', 0) != 0:
                raise ResearchStreamError('STREAM_COMPLETION_INDEX')
            delta = choice.get('delta') or {}
            if not isinstance(delta, dict):
                raise ResearchStreamError('STREAM_INVALID_STRUCTURE')
            if delta.get('tool_calls'):
                raise ResearchStreamError('STREAM_UNEXPECTED_TOOL')
            text = delta.get('content')
            if text is not None:
                if not isinstance(text, str) or (reason is not None and text):
                    raise ResearchStreamError('STREAM_INVALID_CONTENT')
                content.append(text)
            finish = choice.get('finish_reason')
            if finish:
                if reason is not None and finish != reason:
                    raise ResearchStreamError('STREAM_TERMINAL_CONFLICT')
                reason = finish
    if not identity or not model or reason not in ('stop', 'length'):
        raise ResearchStreamError('STREAM_MISSING_TERMINAL')
    return {'id': identity, 'model': model, 'usage': usage,
            'choices': [{'finish_reason': reason, 'message': {'content': ''.join(content)}}]}
