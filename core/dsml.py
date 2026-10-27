"""Parse the declared DSML tool envelope without executing its contents."""
import html
import json
import re

MARKER = r'<(?P<bar>[|｜]+)DSML(?P=bar)\s*'
TOKEN = re.compile(r'<(?P<closing>/?)(?P<bar>[|｜]+)DSML(?P=bar)\s*(?P<kind>calls|invoke|parameter)\b(?P<attrs>[^>]*)>')
ATTRIBUTE = re.compile(r'\b(name|string)\s*=\s*([\"\'])(.*?)\2')


def attributes(text):
    values = {match.group(1): html.unescape(match.group(3)) for match in ATTRIBUTE.finditer(text)}
    if ATTRIBUTE.sub('', text).strip() or not values.get('name'):
        raise ValueError('DSML 属性不完整')
    return values


def parse_dsml(text):
    """Return an ordinary assistant message, or None when DSML is absent."""
    matches = list(TOKEN.finditer(text))
    if not matches:
        return None
    calls = []
    current = None
    parameter = None
    in_calls = False
    seen_calls = False
    visible = []
    cursor = 0
    for match in matches:
        segment = text[cursor:match.start()]
        cursor = match.end()
        if parameter is not None:
            parameter['value'] += segment
        elif current is not None:
            if segment.strip():
                raise ValueError('工具调用中存在未声明文本')
        else:
            visible.append(segment)
        closing = bool(match.group('closing'))
        kind = match.group('kind')
        attrs = match.group('attrs')
        if kind == 'calls':
            if attrs.strip() or current is not None or parameter is not None or in_calls != closing:
                raise ValueError('DSML calls 嵌套不完整')
            in_calls = not closing
            seen_calls = seen_calls or not closing
            continue
        if not in_calls:
            raise ValueError('工具调用不在 calls 内')
        if kind == 'invoke':
            if closing:
                if attrs.strip() or current is None or parameter is not None:
                    raise ValueError('DSML invoke 未闭合')
                calls.append(current)
                current = None
            else:
                if current is not None or parameter is not None:
                    raise ValueError('DSML invoke 嵌套不完整')
                current = {'name': attributes(attrs)['name'], 'arguments': {}}
            continue
        if current is None:
            raise ValueError('工具参数不在 invoke 内')
        if closing:
            if attrs.strip() or parameter is None:
                raise ValueError('DSML parameter 未闭合')
            value = html.unescape(parameter['value'])
            if parameter['string'] == 'false':
                try:
                    value = json.loads(value)
                except json.JSONDecodeError as exc:
                    raise ValueError('DSML 非字符串参数不是有效 JSON') from exc
            elif parameter['string'] != 'true':
                raise ValueError('DSML string 属性必须是 true 或 false')
            name = parameter['name']
            if name in current['arguments']:
                raise ValueError('DSML 参数重复')
            current['arguments'][name] = value
            parameter = None
        else:
            if parameter is not None:
                raise ValueError('DSML parameter 嵌套不完整')
            values = attributes(attrs)
            parameter = {'name': values['name'], 'string': values.get('string', 'true'), 'value': ''}
    if in_calls or current is not None or parameter is not None or not seen_calls:
        raise ValueError('DSML 未完整闭合')
    tail = text[cursor:]
    if tail.strip():
        visible.append(tail)
    if TOKEN.search(tail) or re.search(MARKER, text[:matches[0].start()]):
        raise ValueError('无法识别的 DSML 片段')
    return {'role': 'assistant', 'content': ''.join(visible).strip(),
            'tool_calls': [{'id': '', 'type': 'function',
                            'function': {'name': call['name'],
                                         'arguments': json.dumps(call['arguments'], ensure_ascii=False)}}
                           for call in calls]}
