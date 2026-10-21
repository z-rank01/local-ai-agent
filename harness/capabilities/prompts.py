"""Declarative prompt contributions from trusted, source-installed packages."""
from dataclasses import dataclass
from hashlib import sha256
import json
from pathlib import Path


@dataclass(frozen=True)
class PromptContribution:
    identity: str
    tools: frozenset[str]
    text: str
    fingerprint: str


def load_prompts(packages: Path):
    contributions, errors = [], {}
    for manifest in sorted(packages.glob('*/plugin.json')):
        try:
            package = manifest.parent.resolve()
            if not manifest.resolve().is_relative_to(packages.resolve()):
                raise ValueError('插件提示清单越界')
            metadata = json.loads(manifest.read_text(encoding='utf-8'))
            declarations = metadata.get('prompts', [])
            if not declarations:
                continue
            if metadata.get('id') != package.name or metadata.get('harness_api') != 1:
                raise ValueError('插件提示契约无效')
            if not isinstance(declarations, list):
                raise ValueError('提示贡献须为列表')
            pending = []
            for index, declaration in enumerate(declarations):
                path = (package / declaration['path']).resolve()
                tools = declaration['when_tools']
                if not path.is_relative_to(package) or path.suffix != '.txt':
                    raise ValueError('提示文件必须是插件内的文本文件')
                if not isinstance(tools, list) or not tools or any(not isinstance(t, str) or not t for t in tools):
                    raise ValueError('提示贡献必须声明适用工具')
                raw = path.read_bytes()
                if len(raw) > 128 * 1024:
                    raise ValueError('插件提示超过大小限制')
                identity = f'{package.name}:{index}'
                digest = sha256(json.dumps(declaration, sort_keys=True).encode() + raw).hexdigest()
                pending.append(PromptContribution(identity, frozenset(tools), raw.decode('utf-8').strip(), digest))
            contributions.extend(pending)
        except (OSError, ValueError, KeyError, TypeError, AttributeError) as exc:
            errors[manifest.parent.name] = str(exc)
    return contributions, errors
