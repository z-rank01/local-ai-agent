"""Install versioned project methods into each mounted workspace, once."""

from pathlib import Path
import shutil
import tempfile
import time


def _publish(copied: Path, target: Path) -> None:
    # Windows indexers may briefly hold newly copied files. Keep publication
    # atomic; bounded retries do not overwrite an existing installation.
    for attempt in range(4):
        try:
            copied.rename(target)
            return
        except PermissionError as exc:
            if getattr(exc, 'winerror', None) not in (5, 32, 33) or attempt == 3:
                raise
            time.sleep(0.05 * (2 ** attempt))


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
            _publish(copied, target)
        installed.append(source.name)
    return installed
