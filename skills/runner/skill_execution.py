"""The compatibility executor consumes only host-pinned legacy revisions."""
from contextlib import aclosing
import json
from pathlib import Path
from legacy_skill import LegacyPackage
from skill_version_api import storage
from skill_registry import _WRAPPER_TEMPLATE, _load_skill_config, _save_skill_config, _now_iso
from stream_exec import stream_python


def resolve_execution(name, revision):
    if not revision:
        raise ValueError('缺少宿主固定技能版本，请重新开始运行并核对工具镜像版本')
    package = storage().read(name, revision)
    if not isinstance(package, LegacyPackage):
        raise ValueError('方法包不提供 run(params)，请读取说明与脚本')
    errors = package.listing_entry()['environment_errors']
    if errors:
        raise ValueError('；'.join(errors))
    return Path(package.path) / package.scripts[0]


async def execute(name, path, params, timeout, execution_id):
    async with aclosing(stream_python(_WRAPPER_TEMPLATE.format(skill_path=str(path)), timeout,
                       [json.dumps(params)], execution_id=execution_id)) as stream:
        async for item in stream:
            if item.get('event') == 'result':
                result = item['result']
                result['model_observation'] = {'exit_code': result['exit_code'],
                    'error': '技能执行失败或输出不完整，原始输出保留在本地回执。'}
                if result['exit_code'] == 0:
                    try:
                        result['result'] = json.loads(result['stdout'].strip().splitlines()[-1])
                        result['model_observation'] = {'exit_code': 0, 'result': result['result']}
                    except (ValueError, IndexError):
                        result['error'] = '技能返回结果不是完整 JSON，可能被截断；不把原始输出发送给模型'
                cfg = _load_skill_config(name)
                if cfg:
                    cfg['run_count'] = cfg.get('run_count', 0) + 1
                    cfg['last_run_at'] = _now_iso()
                    _save_skill_config(name, cfg)
            yield item
