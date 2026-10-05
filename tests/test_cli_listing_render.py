"""Listings retain their project/host groups and explicit empty state."""

from rich.console import Console
import pytest

from fleet_cli import cli


def rendered(reports, group_by):
    console = Console(record=True, width=200, no_color=True)
    console.print(cli.render(reports, group_by=group_by, brief=False))
    return console.export_text()


def test_empty_listing_names_the_empty_state():
    assert 'no jobs' in rendered([], 'project')


@pytest.mark.parametrize('group_by', ['project', 'host'])
def test_listing_groups_jobs_from_two_hosts(group_by):
    job = dict(id='job12345', project='shared-project', status='running',
               agent='codex', description='Verify grouping', created_at=0, steps=[])
    reports = [cli.HostReport(cli.Host(name, None), [job], None)
               for name in ('home', 'carbon')]
    output = rendered(reports, group_by)
    assert 'Verify grouping' in output
    assert 'home' in output and 'carbon' in output
    if group_by == 'project':
        assert 'shared-project  2 running · 2 jobs' in output
    else:
        assert 'home  1 running · 1 jobs' in output
        assert 'carbon  1 running · 1 jobs' in output
