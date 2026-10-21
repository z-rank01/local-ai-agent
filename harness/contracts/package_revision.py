"""Fingerprint the executable, image-packaged part of a plugin."""
import hashlib


def package_revision(root):
    digest = hashlib.sha256()
    paths = [root / 'plugin.json', *root.glob('tools/*.yaml'), *root.glob('runtime/**/*.py')]
    for path in sorted(paths):
        if not path.resolve().is_relative_to(root.resolve()):
            raise ValueError('插件资源越界')
        name = path.relative_to(root).as_posix().encode('utf-8')
        data = path.read_bytes()
        digest.update(len(name).to_bytes(8, 'big') + name + len(data).to_bytes(8, 'big') + data)
    return digest.hexdigest()
