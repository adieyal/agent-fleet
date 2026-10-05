"""Copied snapshots retain imported attention history without importing it twice."""
import json
import pytest
from fleet.composition import open_store
from fleet.infrastructure.sqlite.attention_import import import_workspace


def test_relocated_workspace_does_not_duplicate_imported_actions(tmp_path):
    store = open_store()
    first, copied = tmp_path / 'original.json', tmp_path / 'copied.json'
    document = {'attention': {'job:carbon:j:failed:0@1': {'state': 'acknowledged', 'at': 1}}}
    first.write_text(json.dumps(document)); copied.write_text(first.read_text())
    import_workspace(store, first)
    import_workspace(store, copied)
    sequence = store.latest_sequence()
    import_workspace(store, copied)
    assert store.latest_sequence() == sequence
    with store.unit_of_work() as work:
        assert work.connection.execute('SELECT count(*) FROM attention_imported_action').fetchone()[0] == 1
        assert work.connection.execute("SELECT count(*) FROM state_history WHERE subject = ?", ('attention-import:job:carbon:j:failed:0@1',)).fetchone()[0] == 1


def test_relocated_workspace_conflicting_action_is_explicit(tmp_path):
    store = open_store()
    first, copied = tmp_path / 'original.json', tmp_path / 'copied.json'
    first.write_text(json.dumps({'attention': {'j': {'state': 'acknowledged', 'at': 1}}}))
    copied.write_text(json.dumps({'attention': {'j': {'state': 'snoozed', 'at': 2, 'until': 3}}}))
    import_workspace(store, first)
    with pytest.raises(ValueError, match='conflicting imported attention action: j'):
        import_workspace(store, copied)
    with store.unit_of_work() as work:
        assert work.connection.execute('SELECT state FROM attention_imported_action WHERE reference = ?', ('j',)).fetchone()[0] == 'acknowledged'
