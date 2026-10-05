import time
import pytest
from dependency_injector import providers
from fleet_cli import cli


@pytest.mark.parametrize('seconds, expected', [(None, 'unknown'), (0, '0m'), (20, '<1m'), (180, '3m'), (3840, '1h04m'), (7200, '2h00m')])
def test_compact_duration(seconds, expected):
    assert cli.history_duration(seconds) == expected


def test_history_local_time_converts_offset(monkeypatch):
    with monkeypatch.context() as patch:
        patch.setenv('TZ', 'Africa/Johannesburg')
        time.tzset()
        try:
            assert cli.history_local_time('2026-10-01T12:11:59Z') == '2026-10-01 14:11'
            assert cli.history_local_time(None) == 'unknown'
        finally:
            patch.undo()
            time.tzset()


def test_history_text_aligns_columns_and_keeps_unknowns(cli_container, capsys):
    run = dict(start='2026-10-01T12:11:00Z', duration_seconds=3840, status='succeeded', kind='job',
               host='home', work_item=None, label='demo', workspace={'branch': 'feature'},
               workspace_reason=None, commit_count=2, push_count=0, offline_since=None, id='abcdefgh-1')
    unknown = dict(run, start=None, duration_seconds=None, workspace=None, workspace_reason='workspace not recorded',
                   commit_count=None, push_count=None, status='unknown outcome', host='long-host', id='ijklmnop-2')
    cli_container.history_runs.override(providers.Factory(lambda **kwargs: dict(runs=[run, unknown], total=2, empty_reason=None)))
    cli.main(['history', 'runs'], container=cli_container)
    lines = capsys.readouterr().out.splitlines()
    for header, first, second in [('DURATION', '1h04m', 'unknown'), ('STATUS', 'succeeded', 'unknown outcome'),
                                  ('HOST', 'home', 'long-host'), ('RUN', 'abcdefgh', 'ijklmnop')]:
        column = lines[0].index(header)
        assert lines[1][column:].startswith(first)
        assert lines[2][column:].startswith(second)
    assert 'branch unknown · git unknown (workspace not recorded)' in lines[2]
    assert '2 of 2 runs' in lines[-1]
