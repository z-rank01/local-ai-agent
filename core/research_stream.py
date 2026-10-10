"""Accumulate public completion content and terminal metadata, never hidden reasoning."""
import json
import httpx


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
        event = json.loads(raw)
        if event.get('error'):
            raise ValueError('Provider stream error without verified completion')
        current_id, current_model = event.get('id'), event.get('model')
        if (identity and current_id and identity != current_id) or (model and current_model and model != current_model):
            raise ValueError('Mixed stream identities')
        previous = (identity, model)
        identity, model = current_id or identity, current_model or model
        if (identity, model) != previous:
            progress({'response_id': identity, 'response_model': model})
        if isinstance(event.get('usage'), dict):
            usage = event['usage']
        choices = event.get('choices') or []
        if len(choices) > 1:
            raise ValueError('Unexpected multiple completions')
        for choice in choices:
            if choice.get('index', 0) != 0:
                raise ValueError('Unexpected completion index')
            delta = choice.get('delta') or {}
            if delta.get('tool_calls'):
                raise ValueError('Research completion unexpectedly requested a tool')
            text = delta.get('content')
            if text is not None:
                if not isinstance(text, str) or (reason is not None and text):
                    raise ValueError('Content after terminal completion')
                content.append(text)
            finish = choice.get('finish_reason')
            if finish:
                if reason is not None and finish != reason:
                    raise ValueError('Conflicting terminal metadata')
                reason = finish
    if not identity or not model or reason not in ('stop', 'length'):
        raise ValueError('Stream has no verified terminal metadata')
    return {'id': identity, 'model': model, 'usage': usage,
            'choices': [{'finish_reason': reason, 'message': {'content': ''.join(content)}}]}
