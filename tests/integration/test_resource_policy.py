"""The resource adapter follows the presentation's injected mount policy."""
from pathlib import Path
from zipfile import ZipFile, Path as ZipPath

from fleet.container import Container
from fleet_web.resources import resources


def test_checkout_routes_are_injected(tmp_path):
    package = tmp_path / 'members/example'
    package.mkdir(parents=True)
    (tmp_path / 'pyproject.toml').write_text('[tool.uv.workspace]\n')
    adapter = Container().package_resources(
        package='fleet_web', source=package / 'resources.py', member_root='members/example',
        checkout_mounts={'/preview/': 'design/preview'}, temporary_prefix='example-static-')
    assert adapter.checkout_folders() == {'/preview/': tmp_path / 'design/preview'}
    adapter.source = tmp_path / 'installed/resources.py'
    assert adapter.checkout_folders() == {}


def test_materialization_uses_injected_prefix_and_cleans_up(tmp_path):
    archive = tmp_path / 'resources.zip'
    with ZipFile(archive, 'w') as writer:
        writer.writestr('static/index.html', b'example')
    adapter = Container().package_resources(
        package='fleet_web', source=__file__, member_root='unused',
        checkout_mounts={}, temporary_prefix='example-static-')
    with ZipFile(archive) as reader:
        adapter.static = ZipPath(reader, 'static/')
        with adapter.static_directory() as directory:
            assert directory.parent.name.startswith('example-static-')
            assert (directory / 'index.html').read_bytes() == b'example'
        assert not directory.exists()


def test_web_supplies_existing_routes_and_prefix():
    adapter = resources(Container())
    assert adapter.temporary_prefix == 'fleet-web-static-'
    root = Path(__file__).resolve().parents[2]
    assert adapter.checkout_folders() == {
        '/art/bakeoff/': root / 'art/bakeoff', '/concept/': root / 'docs/images/concept'}
