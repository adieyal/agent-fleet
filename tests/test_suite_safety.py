"""Run the actual suite hooks under a disposable HOME, never the live store."""

import os
from pathlib import Path
import subprocess
import sys

import pytest


@pytest.mark.parametrize("operation", [
    "configured_container(path=Path.home() / '.config/fleet/fleet.db').store()",
    "sqlite3.connect(str(Path.home() / '.config/fleet/fleet.db'))",
    "sqlite3.connect((Path.home() / '.config/fleet/fleet.db').as_uri(), uri=True)",
    "(Path.home() / '.config/fleet/config.json').read_text()",
    "(Path.home() / '.config/fleet/config.json').write_text('{}')",
    "os.replace(Path('replacement.json'), Path.home() / '.config/fleet/config.json')",
])
def test_real_config_access_is_rejected_in_subprocess(tmp_path: Path, operation: str) -> None:
    directory = tmp_path / 'home/.config/fleet'
    directory.mkdir(parents=True)
    config = directory / 'config.json'
    config.write_text('{"hosts": {}}')
    (tmp_path / 'replacement.json').write_text('{}')
    source = f"""import os, sqlite3
from pathlib import Path
from fleet.container import configured_container

def test_forbidden():
    {operation}
"""
    result = run_suite(tmp_path, source)
    assert result.returncode != 0, result.stdout
    assert "Tests must not access the user's real Fleet config/store" in result.stdout
    assert config.read_text() == '{"hosts": {}}'
    assert not (directory / 'fleet.db').exists()


def test_browser_marker_follows_fixture_dependencies(tmp_path: Path) -> None:
    result = run_suite(tmp_path, """import pytest
@pytest.fixture
def indirect_page(page):
    return page

def test_indirect(indirect_page):
    raise AssertionError('browser test ran')

def test_plain():
    pass
""", '-m', 'not browser')
    assert result.returncode == 0, result.stdout
    assert '1 passed, 1 deselected' in result.stdout


def test_snapshot_excludes_deck_database_files(tmp_path: Path) -> None:
    directory = tmp_path / 'home/.config/fleet'
    directory.mkdir(parents=True)
    database = directory / 'fleet.db-wal'
    database.write_text('before')
    # A separate process stands in for the deck, which does not run pytest's audit hook.
    result = run_suite(tmp_path, f"""import subprocess, sys

def test_deck_write():
    subprocess.run([sys.executable, '-c', {f'from pathlib import Path; Path({str(database)!r}).write_text("after")'!r}], check=True)
""")
    assert result.returncode == 0, result.stdout
    assert database.read_text() == 'after'


def run_suite(directory: Path, source: str, *arguments: str) -> subprocess.CompletedProcess[str]:
    root = Path(__file__).resolve().parents[1]
    (directory / 'conftest.py').write_text((root / 'tests/conftest.py').read_text())
    (directory / 'fixtures').mkdir()
    (directory / 'fixtures/restoke.json').write_text((root / 'tests/fixtures/restoke.json').read_text())
    (directory / 'test_probe.py').write_text(source)
    environment = {**os.environ, 'HOME': str(directory / 'home'), 'PYTHONPATH': str(root)}
    return subprocess.run(
        [sys.executable, '-m', 'pytest', '-q', '--confcutdir', str(directory), *arguments],
        cwd=directory, env=environment, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        timeout=60,
    )
