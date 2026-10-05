"""Physical context operations and pinned exports stay in infrastructure."""
from pathlib import Path

from fleet.infrastructure.files import LocalFiles
from fleet.infrastructure.git import RepositoryWriter


def test_repository_creation_is_idempotent_and_export_reads_the_requested_revision(tmp_path):
    writer = RepositoryWriter()
    root = tmp_path / 'management'
    writer.create(root)
    body = root / 'constitution.md'
    body.write_text('Pinned constitution\n')
    writer.git(root, 'add', 'constitution.md')
    writer.git(root, '-c', 'user.name=test', '-c', 'user.email=test@localhost',
               '-c', 'commit.gpgsign=false', 'commit', '-qm', 'Pinned')
    pinned = writer.git(root, 'rev-parse', 'HEAD')
    body.write_text('New constitution\n')
    writer.git(root, 'add', 'constitution.md')
    writer.git(root, '-c', 'user.name=test', '-c', 'user.email=test@localhost',
               '-c', 'commit.gpgsign=false', 'commit', '-qm', 'New')
    writer.create(root)
    assert body.read_text() == 'New constitution\n'
    target = tmp_path / 'CONSTITUTION.md'
    assert writer.export(str(root), 'constitution.md', pinned, target) == str(target)
    assert target.read_text() == 'Pinned constitution\n'


def test_local_reference_search_uses_cwd_then_calling_job_home(tmp_path, monkeypatch):
    files = LocalFiles()
    working, home = tmp_path / 'working', tmp_path / 'home'
    working.mkdir()
    job = home / '.fleet/jobs/j'
    job.mkdir(parents=True)
    (job / 'report.md').write_text('job report')
    monkeypatch.chdir(working)
    monkeypatch.setenv('HOME', str(home))
    monkeypatch.setenv('FLEET_JOB_ID', 'j')
    assert files.find_reference('report.md') == job / 'report.md'
    (working / 'report.md').write_text('cwd report')
    assert files.find_reference('report.md') == working / 'report.md'
    assert files.find_reference('~/.fleet/jobs/j/report.md') == job / 'report.md'
    assert files.find_reference('missing.md') is None
    assert files.exists('report.md')
    assert files.absolute('report.md') == str(working / 'report.md')


def test_destination_and_temporary_directories_are_owned_by_the_adapter(tmp_path):
    files = LocalFiles()
    destination = files.create_directory(str(tmp_path / 'nested/destination'))
    assert destination == tmp_path / 'nested/destination'
    assert destination.is_dir()
    with files.temporary_directory(prefix='fleet-guidance-') as directory:
        path = Path(directory)
        assert path.is_dir()
    assert not path.exists()


def test_storage_counts_regular_files_and_reports_empty_directories(tmp_path):
    files = LocalFiles()
    directory = tmp_path / 'projects'
    (directory / 'nested').mkdir(parents=True)
    (directory / 'one').write_bytes(b'123')
    (directory / 'nested/two').write_bytes(b'4567')
    (directory / 'alias').symlink_to(directory / 'one')
    (directory / 'dir-alias').symlink_to(directory / 'nested', target_is_directory=True)
    assert files.usage(directory) == {'path': str(directory), 'files': 2, 'bytes': 7}
    absent = tmp_path / 'traces'
    assert files.usage(absent) == {'path': str(absent), 'files': 0, 'bytes': 0}
