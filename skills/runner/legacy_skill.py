"""Read legacy single-file skills without importing their Python code."""
import ast
from dataclasses import dataclass, field
from importlib import metadata
from pathlib import Path
import sys
from pip._vendor.packaging.requirements import Requirement
from pip._vendor.packaging.utils import canonicalize_name


def dependency_errors(requirements):
    installed = {canonicalize_name(item.metadata['Name']): item.version
                 for item in metadata.distributions(path=[*sys.path, '/packages'])
                 if item.metadata['Name']}
    errors = []
    for value in requirements:
        requirement = Requirement(value)
        if requirement.url:
            errors.append('依赖不能使用直接下载地址：' + value)
        elif requirement.extras:
            errors.append('尚不支持自动核验 extras 依赖，请将实际依赖逐项声明：' + value)
        elif not requirement.marker or requirement.marker.evaluate():
            version = installed.get(canonicalize_name(requirement.name))
            if version is None or version not in requirement.specifier:
                errors.append('Python 环境缺少符合版本的依赖：' + value)
    return errors


@dataclass
class LegacyPackage:
    name: str
    description: str
    body: str
    path: str
    version: str
    parameters: dict
    environment_dependencies: list[str]
    scripts: list[str]
    permissions: list[str] = field(default_factory=list)
    dependencies: list[str] = field(default_factory=list)
    references: list[str] = field(default_factory=list)
    assets: list[str] = field(default_factory=list)

    def listing_entry(self):
        return {'name': self.name, 'kind': 'python', 'description': self.description[:2000],
                'version': self.version, 'parameters': self.parameters,
                'permissions': self.permissions, 'dependencies': [],
                'environment_dependencies': self.environment_dependencies,
                'environment_errors': dependency_errors(self.environment_dependencies)}


def read_package(path: Path):
    if path.stat().st_size > 1024 * 1024:
        raise ValueError('旧式 Python Skill 超过 1 MiB 限制')
    code = path.read_text(encoding='utf-8')
    tree = ast.parse(code)
    value = next((ast.literal_eval(node.value) for node in tree.body
                  if isinstance(node, ast.Assign) and any(isinstance(target, ast.Name)
                  and target.id == 'SKILL_METADATA' for target in node.targets)), None)
    if not isinstance(value, dict) or not isinstance(value.get('description'), str) or not value['description'].strip():
        raise ValueError('旧式 Python Skill 缺少有效 SKILL_METADATA.description')
    if not any(isinstance(node, ast.FunctionDef) and node.name == 'run' for node in tree.body):
        raise ValueError('旧式 Python Skill 缺少 run(params)')
    requirements = value.get('dependencies', [])
    if not isinstance(requirements, list) or any(not isinstance(item, str) for item in requirements):
        raise ValueError('Python 依赖必须是字符串列表')
    for requirement in requirements:
        Requirement(requirement)  # Validate syntax; never install anything here.
    parameters = value.get('parameters', {'type': 'object', 'properties': {}})
    if not isinstance(parameters, dict):
        raise ValueError('旧式 Python Skill 参数必须为对象 schema')
    permissions = value.get('permissions', [])
    if not isinstance(permissions, list) or any(not isinstance(item, str) or not item for item in permissions):
        raise ValueError('旧式 Python Skill 权限必须是非空字符串列表')
    return LegacyPackage(path.stem, value['description'], code, str(path.parent),
                         str(value.get('version', '1.0.0')), parameters, requirements, [path.name], permissions)
