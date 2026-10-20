"""Install versioned project methods into each mounted workspace, once."""

from pathlib import Path
import shutil
import tempfile


def install_methods(workspace: Path = Path('/workspace'), packages: Path | None = None) -> list[str]:
    packages = packages or Path(__file__).with_name('packages')
    root = workspace / 'skills'
    if root.is_symlink():
        raise ValueError('Workspace skills directory cannot be a symbolic link')
    root.mkdir(parents=True, exist_ok=True)
    installed = []
    for source in sorted(packages.iterdir()):
        if not source.is_dir() or not (source / 'SKILL.md').is_file():
            continue
        target = root / source.name
        # A version is immutable from the installer's perspective, including user edits.
        if target.exists() or target.is_symlink():
            continue
        with tempfile.TemporaryDirectory(prefix='.install-', dir=root) as staging:
            copied = Path(staging) / source.name
            shutil.copytree(source, copied, ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))
            copied.rename(target)
        installed.append(source.name)
    return installed
