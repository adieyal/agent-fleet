"""Records select repositories and pinned files without touching the filesystem."""
from pathlib import Path
from unittest.mock import Mock, patch

import pytest

from fleet.modules.records import RecordsFacade


def test_provide_delegates_creation_and_keeps_registered_repositories(tmp_path):
    writer, workspace, work = Mock(), Mock(), Mock()
    workspace.management_repository.side_effect = ValueError('unregistered')
    writer.root.return_value = '/managed/p'
    work.legacy_summaries.return_value = []
    facade = RecordsFacade(Mock(), writer, workspace, lambda: work, home=tmp_path)
    with patch.object(Path, 'mkdir', side_effect=AssertionError('facade touched disk')):
        facade.provide('p', actor='author')
    writer.create.assert_called_once_with(tmp_path / 'p')
    writer.root.assert_called_once_with(tmp_path / 'p')
    workspace.register_management_repository.assert_called_once_with('p', '/managed/p', actor='author')
    writer.git.assert_not_called()
    workspace.management_repository.side_effect = None
    workspace.management_repository.return_value = '/registered/elsewhere'
    facade.provide('p', actor='author')
    assert writer.create.call_count == 1


def test_guidance_export_delegates_exact_pinned_revisions_without_local_writes(tmp_path):
    writer, workspace = Mock(), Mock()
    workspace.management_repository.return_value = '/managed/p'
    writer.export.side_effect = lambda root, path, revision, target: str(target)
    facade = RecordsFacade(Mock(), writer, workspace, Mock())
    guidance = {'project': 'p', 'constitution': {'path': 'constitution.md', 'revision': 'old-c'},
                'charter': {'path': 'charters/epic.md', 'revision': 'old-e'}}
    with patch.object(Path, 'write_text', side_effect=AssertionError('facade touched disk')):
        written = facade.write_guidance_files(guidance, tmp_path)
    assert written == [str(tmp_path / 'CONSTITUTION.md'), str(tmp_path / 'CHARTER.md')]
    assert [call.args for call in writer.export.call_args_list] == [
        ('/managed/p', 'constitution.md', 'old-c', tmp_path / 'CONSTITUTION.md'),
        ('/managed/p', 'charters/epic.md', 'old-e', tmp_path / 'CHARTER.md')]
    writer.read.assert_not_called()


def test_export_errors_remain_visible(tmp_path):
    writer = Mock()
    writer.export.side_effect = OSError('destination unavailable')
    facade = RecordsFacade(Mock(), writer, Mock(), Mock())
    with pytest.raises(OSError, match='destination unavailable'):
        facade.write_guidance_files({'project': 'p', 'constitution': {'path': 'constitution.md', 'revision': 'r'},
                                     'charter': None}, tmp_path)
