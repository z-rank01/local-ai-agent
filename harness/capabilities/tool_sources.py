"""Discover static tool data in explicitly trusted, bundled package roots."""
import json
from pathlib import Path


def tool_sources(directory: Path, packages: Path | None = None):
    yield from sorted(directory.glob('*.yaml'))
    if packages is None:
        return
    for manifest in sorted(packages.glob('*/plugin.json')):
        try:
            package = manifest.parent.resolve()
            if not package.is_relative_to(packages.resolve()):
                continue
            metadata = json.loads(manifest.read_text(encoding='utf-8'))
            if metadata.get('harness_api') != 1 or metadata.get('id') != package.name:
                continue
            if metadata.get('tool_registration') == 'managed':
                continue  # Explicit activation owns registration; importing metadata does not activate it.
            relative = metadata.get('tool_directory')
            if not isinstance(relative, str):
                continue
            tools = (package / relative).resolve()
            if not tools.is_relative_to(package):
                continue
            for path in sorted(tools.glob('*.yaml')):
                if path.resolve().is_relative_to(package):
                    yield path
        except (OSError, ValueError, AttributeError):
            # Inventory exposes invalid manifests; one bad package cannot hide core tools.
            continue
