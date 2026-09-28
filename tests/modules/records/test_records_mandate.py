import json
from unittest.mock import Mock

import pytest

from fleet.modules.records import Mandate, RecordsFacade


@pytest.mark.parametrize('body', ['null', '[]', '{', '{"goal":"Ship"}', json.dumps(dict(
    goal='Ship', constraints='none', decision_authority=[], escalation_conditions=[], criteria_it_may_judge=[]))])
def test_malformed_mandate_is_explicitly_rejected(body):
    with pytest.raises(ValueError, match='invalid mandate'):
        Mandate.parse(body)


def test_all_mandate_fields_are_preserved():
    fields = dict(goal='Ship', constraints=['No deploy'], decision_authority=['dispatch'],
                  escalation_conditions=['Risk'], criteria_it_may_judge=['c1'])
    mandate = Mandate.parse(json.dumps(fields))
    assert vars(mandate) == fields


def test_write_mandate_rejects_unknown_commands_before_authoring():
    records = RecordsFacade(Mock(), Mock(), Mock(), Mock())
    records.authoring = Mock()
    fields = dict(goal='Ship', constraints=[], escalation_conditions=[], criteria_it_may_judge=[],
                  decision_authority=['dispatch', 'Dispatch actions for slice 6 tasks', 'typo'])
    with pytest.raises(ValueError) as rejected:
        records.write_mandate('p', 'mandate.json', json.dumps(fields), key='m', actor='user')
    message = str(rejected.value)
    assert 'decision_authority' in message
    assert 'Dispatch actions for slice 6 tasks' in message
    assert 'typo' in message
    assert 'valid names:' in message
    for name in ('update_progress', 'raise_attention', 'dispatch', 'summary', 'record_decision', 'accept'):
        assert name in message
    records.authoring.write.assert_not_called()
