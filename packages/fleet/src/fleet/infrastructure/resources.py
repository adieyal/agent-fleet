"""Packaged static bytes and a filesystem view for startup fingerprints and directory scans."""
import hashlib

from contextlib import contextmanager
from importlib.resources import files
from pathlib import Path, PurePosixPath
from tempfile import TemporaryDirectory


def copy_tree(source, target):
    target.mkdir()
    for child in source.iterdir():
        if child.name in ('.', '..') or '/' in child.name or '\\' in child.name:
            raise ValueError(f'invalid package resource name: {child.name}')
        destination = target / child.name
        if child.is_dir():
            copy_tree(child, destination)
        else:
            destination.write_bytes(child.read_bytes())


class PackageResources:
    def __init__(self, package, source, member_root):
        self.static = files(package).joinpath("static")
        self.source = source
        self.member_root = member_root

    def read_static(self, name):
        path = PurePosixPath(name)
        if path.is_absolute() or '..' in path.parts or '\\' in name or '\x00' in name:
            raise ValueError(f'static path outside package resources: {name}')
        target = self.static.joinpath(*path.parts)
        if isinstance(target, Path) and not target.resolve().is_relative_to(self.static.resolve()):
            raise ValueError(f'static path outside package resources: {name}')
        return target.read_bytes()

    @contextmanager
    def static_directory(self):
        if isinstance(self.static, Path):
            yield self.static.resolve()
        else:
            with TemporaryDirectory(prefix='fleet-web-static-') as directory:
                root = Path(directory) / 'static'
                copy_tree(self.static, root)
                yield root

    def checkout_folders(self):
        """Only mount art from the checkout that owns this source package, never from the working directory."""
        package = Path(self.source).resolve().parent
        for candidate in package.parents:
            if (candidate / self.member_root).resolve() == package:
                metadata = candidate / 'pyproject.toml'
                if metadata.is_file() and '[tool.uv.workspace]' in metadata.read_text():
                    return {'/art/bakeoff/': candidate / 'art/bakeoff',
                            '/concept/': candidate / 'docs/images/concept'}
        return {}

    def build_id(self, root):
        digest = hashlib.sha256()
        for path in sorted(p for p in root.rglob("*") if p.is_file() and "__pycache__" not in p.parts):
            stat = path.stat()
            digest.update(f"{path.relative_to(root)}\0{stat.st_size}\0{stat.st_mtime_ns}\n".encode())
        return digest.hexdigest()[:16]

    def app_files(self, root, directories):
        return {"/" + path.relative_to(root).as_posix(): self.read_static(path.relative_to(root).as_posix())
                for directory in directories for path in sorted((root / directory).rglob("*")) if path.is_file()}
