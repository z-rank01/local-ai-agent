"""Configured model routing, streaming transports and durable optional call budget."""
from __future__ import annotations

from contextlib import closing
import asyncio
import json
import os
import re
import sqlite3
import time
import uuid
from pathlib import Path

import httpx

from . import config
from .llm_client import LLMClient

# Catalog discovery refreshes at most this often; /api/models and /api/providers
# are usually called together, so a short TTL avoids double probing.
_CATALOG_TTL_SECONDS = 30

# Provider catalogs also list non-chat models that cannot serve this harness.
_NON_CHAT_MODEL = re.compile(r'embedding|rerank|tts|asr|speech|audio|image|video|ocr|docmind', re.IGNORECASE)

# Thinking-control parameter conventions differ per provider; this table maps
# each cloud provider to the prefix that marks its own model family. Models
# outside the family (third-party hosted ids like "ZHIPU/..." on DashScope)
# get no switch, because foreign thinking parameters may silently do nothing.
#   qwen (DashScope compatible mode): enable_thinking (bool) + thinking_budget (int)
#   ollama (local): think (bool), no strength — handled via spec['kind'] == 'local'
_CLOUD_THINKING = {
    'qwen': {'family_prefix': 'qwen'},
}


def thinking_capability(spec):
    """Thinking controls offered for a model: 'switch_budget', 'switch' or None.

    None means the provider default applies and no toggle is shown.
    """
    if spec.get('kind') == 'local':
        return 'switch'
    conf = _CLOUD_THINKING.get(spec.get('provider_id', ''))
    if conf and spec.get('model', '').lower().startswith(conf['family_prefix']):
        return 'switch_budget'
    return None


def credential_path():
    return config.PROJECT_ROOT / 'data' / 'private' / 'model-keys.json'


def read_key(env_name):
    value = os.environ.get(env_name, '')
    if value:
        return value
    path = credential_path()
    if path.exists():
        return json.loads(path.read_text(encoding='utf-8')).get(env_name, '')
    return ''


def save_key(env_name, value):
    path = credential_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    data = json.loads(path.read_text(encoding='utf-8')) if path.exists() else {}
    data[env_name] = value
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps(data), encoding='utf-8')
    temporary.chmod(0o600)
    temporary.replace(path)


async def _probe_ollama_models(base_url):
    """Installed Ollama model names, or None when the server is unreachable."""
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(3, connect=2), trust_env=False) as client:
            resp = await client.get(base_url.rstrip('/') + '/api/tags')
            if resp.status_code != 200:
                return None
            return [m.get('name') for m in resp.json().get('models', []) if m.get('name')]
    except Exception:
        return None


async def _probe_compatible_models(spec):
    """Model ids exposed by an OpenAI-compatible provider, or None on failure.

    Skipped entirely when no API key is configured, so listing models never
    makes unauthenticated or unexpected outbound calls.
    """
    key = read_key(spec.get('api_key_env', ''))
    if not key:
        return None
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(10, connect=5), trust_env=False) as client:
            resp = await client.get(spec['base_url'].rstrip('/') + '/models',
                                    headers={'Authorization': f'Bearer {key}'})
            if resp.status_code != 200:
                return None
            rows = resp.json().get('data')
            if not isinstance(rows, list) or any(not isinstance(row, dict) or
                    not isinstance(row.get('id'), str) or not row['id'] for row in rows):
                return None
            return [row['id'] for row in rows]
    except Exception:
        return None


class CallBudget:
    def __init__(self, path, limit=0):
        self.path, self.limit = str(path), limit

    def status(self):
        if not Path(self.path).exists():
            return {'used':0, 'limit':self.limit}
        with closing(sqlite3.connect(self.path)) as db:
            used = db.execute('SELECT COUNT(*) FROM calls').fetchone()[0]
        return {'used':used, 'limit':self.limit}

    def reserve(self, model):
        if not self.limit:
            return
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        with closing(sqlite3.connect(self.path, timeout=20)) as db, db:
            db.execute('CREATE TABLE IF NOT EXISTS calls (id TEXT PRIMARY KEY, model TEXT, at TEXT DEFAULT CURRENT_TIMESTAMP)')
            db.execute('BEGIN IMMEDIATE')
            used = db.execute('SELECT COUNT(*) FROM calls').fetchone()[0]
            if used >= self.limit:
                raise RuntimeError(f'本批模型调用已达到 {self.limit} 次，已停止调用；已有结果保留。')
            db.execute('INSERT INTO calls(id,model) VALUES (?,?)', (uuid.uuid4().hex, model))

    def begin_research(self, model, execution_hash, input_hash, parameters):
        """Audit authorized research in the existing ledger, without a cumulative cost gate."""
        path = Path(self.path)
        path.parent.mkdir(parents=True, exist_ok=True)
        existed = path.exists()
        call_id = uuid.uuid4().hex
        with closing(sqlite3.connect(self.path, timeout=20)) as db:
            installed = db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='research_receipts'").fetchone()
            if existed and not installed:
                backup = path.with_name(path.stem + '.before-research.sqlite')
                # One BFF process owns the ledger; this synchronous migration precedes outbound IO.
                if not backup.exists():
                    temporary = backup.with_name('.ledger-backup-' + uuid.uuid4().hex + '.sqlite')
                    with closing(sqlite3.connect(str(temporary))) as destination:
                        db.backup(destination)
                        if destination.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
                            raise RuntimeError('模型账本备份校验失败，未执行研究请求')
                    temporary.replace(backup)
            with db:
                db.execute('CREATE TABLE IF NOT EXISTS calls (id TEXT PRIMARY KEY, model TEXT, at TEXT DEFAULT CURRENT_TIMESTAMP)')
                db.execute('CREATE TABLE IF NOT EXISTS research_receipts ('
                           'call_id TEXT PRIMARY KEY, execution_hash TEXT NOT NULL, input_hash TEXT NOT NULL, '
                           'status TEXT NOT NULL, receipt TEXT NOT NULL, updated_at TEXT DEFAULT CURRENT_TIMESTAMP)')
                db.execute('INSERT INTO calls(id,model) VALUES (?,?)', (call_id, model))
                db.execute('INSERT INTO research_receipts(call_id,execution_hash,input_hash,status,receipt) '
                           'VALUES (?,?,?,?,?)', (call_id, execution_hash, input_hash, 'RUNNING',
                            json.dumps({'model_params': parameters}, ensure_ascii=False)))
        return call_id

    def finish_research(self, call_id, receipt):
        # Never save prompts, source text, generated text, credentials or hidden reasoning here.
        allowed = {'status', 'execution_hash', 'model_params', 'finish_reason', 'response_id',
                   'response_model', 'usage', 'usage_status', 'error', 'http_status',
                   'rejection_category', 'failure_category', 'input_chars'}
        public_receipt = {key: value for key, value in receipt.items() if key in allowed}
        from .research_errors import safe_error_code, safe_request_id
        for field, validator in (('provider_error_code', safe_error_code), ('provider_request_id', safe_request_id)):
            checked = validator(receipt.get(field))
            if checked is not None:
                public_receipt[field] = checked
        if receipt.get('provider_request_id_status') in ('REPORTED', 'CONFLICT', 'NOT_REPORTED'):
            public_receipt['provider_request_id_status'] = receipt['provider_request_id_status']
        with closing(sqlite3.connect(self.path, timeout=20)) as db, db:
            db.execute('UPDATE research_receipts SET status=?,receipt=?,updated_at=CURRENT_TIMESTAMP WHERE call_id=?',
                       (receipt['status'], json.dumps(public_receipt, ensure_ascii=False), call_id))


class CompatibleClient:
    _supports_tools = True
    observes_native_requests = True
    cloud = True

    def __init__(self, spec, budget):
        self.spec, self.budget = spec, budget
        self.model = spec['model']
        self._client = httpx.AsyncClient(timeout=httpx.Timeout(120, connect=20), trust_env=False)

    @property
    def supports_source_read_choice(self):
        # Capability was checked for this exact catalogue entry; never turn off
        # thinking or switch models to make a selected call acceptable.
        return (self.spec.get('source_read_tool_choice', {}).get('mode') == 'named_non_thinking'
                and self.spec.get('options', {}).get('enable_thinking') is False)

    async def chat_stream_with_tools(self, messages, tools=None, *, selected_source_tool=None, request_observer=None):
        if selected_source_tool is not None:
            names = {item.get('function', {}).get('name') for item in tools or []}
            if (not self.supports_source_read_choice or selected_source_tool not in names or
                    selected_source_tool not in ('stock_public_evidence_find', 'stock_public_evidence_read')):
                raise RuntimeError('所选模型或当前授权工具不支持指定来源读取；未改变思考、模型或工具权限。')
        key = read_key(self.spec['api_key_env'])
        if not key:
            raise RuntimeError('所选模型尚未配置 API Key，请在 Web 右侧模型设置填写密钥。')
        clean = []
        for m in messages:
            item = {k: v for k, v in m.items() if k in ('role', 'content', 'tool_calls', 'tool_call_id', 'reasoning_content')}
            if item.get('tool_calls'):
                item['tool_calls'] = [dict(t, function=dict(t['function'], arguments=json.dumps(t['function']['arguments'], ensure_ascii=False) if isinstance(t['function'].get('arguments'), dict) else t['function'].get('arguments', '{}'))) for t in item['tool_calls']]
            clean.append(item)
        payload = dict(self.spec.get('options', {}))
        payload.update(model=self.model, messages=clean, stream=True)
        if tools:
            payload['tools'] = tools
        if selected_source_tool:
            payload['tool_choice'] = {'type': 'function', 'function': {'name': selected_source_tool}}
        call_id = self.budget.reserve(self.spec['id'])
        from .native_response import NativeStreamReceipt
        receipt = NativeStreamReceipt(uuid.uuid4().hex)
        if request_observer:
            import hashlib
            request_observer('native_model_request', {
                'call_id': call_id, 'model': self.spec['id'], 'local_attempt_id': receipt.attempt_id,
                'parameters': {key: value for key, value in payload.items() if key not in ('messages', 'tools')},
                'tool_names': [item['function']['name'] for item in tools or []],
                'message_count': len(clean),
                'input_chars': len(json.dumps(clean,ensure_ascii=False)),
                'input_hash': hashlib.sha256(json.dumps(clean,ensure_ascii=False,sort_keys=True).encode()).hexdigest()})
        accumulated = {'role': 'assistant', 'content': '', 'reasoning_content': ''}
        calls, finished, thinking_open = {}, False, False
        try:
            async with self._client.stream('POST', self.spec['base_url'].rstrip('/') + '/chat/completions',
                headers={'Authorization': f'Bearer {key}'}, json=payload) as response:
                receipt.headers(response.status_code,response.headers)
                if response.status_code >= 400:
                    receipt.failure_category='HTTP_REJECTED'
                    try:
                        raw_error=await response.aread()
                        receipt.rejection_body(json.loads(raw_error),response.headers)
                    except (ValueError,httpx.RequestError):
                        pass  # The HTTP rejection is known even if its body is unavailable.
                    raise RuntimeError(f'所选模型请求失败（HTTP {response.status_code}），未切换模型。')
                async for line in response.aiter_lines():
                    if not line.startswith('data:'):
                        continue
                    raw = line[5:].strip()
                    if raw == '[DONE]':
                        break
                    try:
                        chunk = json.loads(raw)
                    except ValueError:
                        receipt.failure_category='STREAM_PROTOCOL_ERROR'
                        raise
                    receipt.chunk(chunk)
                    if chunk.get('error'):
                        receipt.failure_category='STREAM_PROVIDER_ERROR'
                        raise RuntimeError('模型流返回错误，已保留收到的内容。')
                    for choice in chunk.get('choices', []):
                        delta = choice.get('delta', {})
                        reasoning = delta.get('reasoning_content') or ''
                        if reasoning:
                            accumulated['reasoning_content'] += reasoning
                            if not thinking_open:
                                yield '<think>', None
                                thinking_open = True
                            yield reasoning, None
                        content = delta.get('content') or ''
                        if content:
                            if thinking_open:
                                yield '</think>', None
                                thinking_open = False
                            accumulated['content'] += content
                            yield content, None
                        for call in delta.get('tool_calls', []):
                            target = calls.setdefault(call['index'], {'id': '', 'type': 'function', 'function': {'name': '', 'arguments': ''}})
                            if call.get('id'):
                                target['id'] = call['id']
                            for field in ('name', 'arguments'):
                                target['function'][field] += call.get('function', {}).get(field) or ''
                        reason = choice.get('finish_reason')
                        if reason:
                            if reason not in ('stop', 'tool_calls'):
                                receipt.failure_category='OUTPUT_TRUNCATED' if reason=='length' else 'UNSUPPORTED_FINISH'
                                raise RuntimeError('模型输出未完整结束，已保留收到的内容。')
                            finished = True
            if thinking_open:
                yield '</think>', None
            if not finished:
                receipt.failure_category='EARLY_EOF'
                raise RuntimeError('模型连接提前中断，已保留收到的内容。')
            if calls:
                accumulated['tool_calls'] = [calls[i] for i in sorted(calls)]
            if selected_source_tool and (not calls or any(call['function']['name'] != selected_source_tool for call in calls.values())):
                receipt.failure_category='TOOL_CHOICE_NOT_HONOURED'
                error = RuntimeError('供应商未返回指定的来源工具调用；原结果保留，未自动重发或降级。')
                error.source_visible_response = accumulated.get('content', '')
                raise error
            if not accumulated['content'] and not calls:
                receipt.failure_category='EMPTY_RESULT'
                raise RuntimeError('模型没有返回回答或工具调用，请重试本轮。')
            receipt.delivery_status='COMPLETE'
            yield '', accumulated
        except httpx.RequestError as error:
            receipt.delivery_status='FAILED'
            if receipt.failure_category is None:
                receipt.failure_category='NETWORK_TIMEOUT' if isinstance(error,httpx.TimeoutException) else 'NETWORK_ERROR'
            raise RuntimeError('无法连接所选模型或响应超时；没有自动重试或切换模型。') from None
        except (asyncio.CancelledError,GeneratorExit):
            if receipt.delivery_status!='COMPLETE':
                receipt.delivery_status='CANCELLED'
                receipt.failure_category='CALLER_CANCELLED'
            raise
        except BaseException:
            receipt.delivery_status='FAILED'
            if receipt.failure_category is None:
                receipt.failure_category='STREAM_PROTOCOL_ERROR'
            raise
        finally:
            if request_observer:
                request_observer('native_model_result', {'call_id':call_id,'model':self.spec['id'], **receipt.result()})

    async def chat(self, system, user_message):
        result = ''
        async for _, message in self.chat_stream_with_tools([{'role': 'system', 'content': system}, {'role': 'user', 'content': user_message}]):
            if message:
                result = message.get('content', '')
        return result

    async def close(self):
        await self._client.aclose()


class ModelRegistry:
    def __init__(self):
        self.budget = CallBudget(config.PROJECT_ROOT / 'data' / 'model-calls.sqlite', int(os.environ.get('MODEL_CALL_LIMIT', '0')))
        static_specs = [{'id': f'ollama:{config.OLLAMA_MODEL}', 'provider_id': 'ollama', 'provider_name': 'Ollama',
                       'model': config.OLLAMA_MODEL, 'base_url': config.OLLAMA_BASE_URL, 'kind': 'local'}]
        catalog_path = Path(os.environ.get('MODEL_CATALOG', str(config.PROJECT_ROOT / 'config' / 'models.json')))
        if catalog_path.exists():
            for item in json.loads(catalog_path.read_text(encoding='utf-8-sig')):
                item = dict(item)
                item['id'] = f"{item['provider_id']}:{item['model']}"
                static_specs.append(item)
        self._static_specs = static_specs
        # Merged snapshot (static + discovered) that resolve() matches against.
        self.specs = list(static_specs)
        self.clients = {}
        self.default = os.environ.get('DEFAULT_MODEL', static_specs[0]['id'])
        self._catalog_fetched_at = 0.0
        self._catalog_lock = asyncio.Lock()

    async def catalog(self, refresh=False):
        """Static specs merged with models discovered from the providers themselves.

        Static entries win. A failed cloud catalog probe retains previously
        discovered models for the exact same provider endpoint/key selector;
        it does not prove model removal or authorize a fallback model.
        """
        async with self._catalog_lock:
            now = time.monotonic()
            if not refresh and self._catalog_fetched_at and now - self._catalog_fetched_at < _CATALOG_TTL_SECONDS:
                return self.specs
            merged = {s['id']: s for s in self._static_specs}
            ollama_names = await _probe_ollama_models(config.OLLAMA_BASE_URL)
            if ollama_names:
                for name in ollama_names:
                    if _NON_CHAT_MODEL.search(name):
                        continue
                    merged.setdefault(f'ollama:{name}', {
                        'id': f'ollama:{name}', 'provider_id': 'ollama', 'provider_name': 'Ollama',
                        'model': name, 'base_url': config.OLLAMA_BASE_URL, 'kind': 'local'})
            cloud_specs = {}
            for spec in self._static_specs:
                if spec.get('kind') == 'cloud':
                    cloud_specs.setdefault(spec['provider_id'], spec)
            for provider_id, spec in cloud_specs.items():
                names = await _probe_compatible_models(spec)
                if names is None:
                    for previous in self.specs:
                        if (previous.get('provider_id') == provider_id and
                                previous.get('base_url') == spec.get('base_url') and
                                previous.get('api_key_env', '') == spec.get('api_key_env', '') and
                                previous.get('kind') == 'cloud'):
                            merged.setdefault(previous['id'], {**previous,
                                'discovery_status': 'RETAINED_AFTER_PROBE_FAILURE'})
                for name in names or []:
                    if _NON_CHAT_MODEL.search(name):
                        continue
                    merged.setdefault(f'{provider_id}:{name}', {
                        'id': f'{provider_id}:{name}', 'provider_id': provider_id,
                        'provider_name': spec['provider_name'], 'model': name,
                        'base_url': spec['base_url'], 'api_key_env': spec.get('api_key_env', ''),
                        'kind': 'cloud', 'discovery_status': 'DISCOVERED'})
            self.specs = list(merged.values())
            self._catalog_fetched_at = now
            return self.specs

    def resolve(self, provider=None, model=None, fallback=None):
        key = f'{provider}:{model}' if provider and model else model or fallback or self.default
        candidates = [s for s in self.specs if s['id'] == key or (s['model'] == key and not provider)]
        if len(candidates) != 1:
            raise ValueError('所选模型未配置或名称不唯一，请重新选择模型。')
        spec = candidates[0]
        if spec['id'] not in self.clients:
            if spec['kind'] == 'local':
                client = LLMClient(spec['base_url'], spec['model'])
                client.cloud = False
            else:
                client = CompatibleClient(spec, self.budget)
            self.clients[spec['id']] = client
        return spec, self.clients[spec['id']]

    async def close(self):
        for client in self.clients.values():
            await client.close()
