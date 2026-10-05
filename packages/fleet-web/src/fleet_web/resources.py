"""Packaged static bytes and a filesystem view for startup fingerprints and directory scans."""
from contextlib import contextmanager
from importlib.resources import files
from pathlib import Path, PurePosixPath
from tempfile import TemporaryDirectory

STATIC = files('fleet_web').joinpath('static')


def read_static(name):
    path = PurePosixPath(name)
    if path.is_absolute() or '..' in path.parts or '\\' in name or '\x00' in name:
        raise ValueError(f'static path outside package resources: {name}')
    target = STATIC.joinpath(*path.parts)
    if isinstance(target, Path) and not target.resolve().is_relative_to(STATIC.resolve()):
        raise ValueError(f'static path outside package resources: {name}')
    return target.read_bytes()


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


@contextmanager
def static_directory():
    if isinstance(STATIC, Path):
        yield STATIC.resolve()
    else:
        with TemporaryDirectory(prefix='fleet-web-static-') as directory:
            root = Path(directory) / 'static'
            copy_tree(STATIC, root)
            yield root


def checkout_folders():
    """Only mount art from the checkout that owns this source package, never from the working directory."""
    package = Path(__file__).resolve().parent
    for candidate in package.parents:
        if (candidate / 'packages/fleet-web/src/fleet_web').resolve() == package:
            metadata = candidate / 'pyproject.toml'
            if metadata.is_file() and '[tool.uv.workspace]' in metadata.read_text():
                return {'/art/bakeoff/': candidate / 'art/bakeoff',
                        '/concept/': candidate / 'docs/images/concept'}
    return {}
