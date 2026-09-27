import json

import pytest

from fleet.modules.records import Mandate


@pytest.mark.parametrize('body', ['null', '[]', '{', '{"goal":"Ship"}', json.dumps(dict(
    goal='Ship', constraints='none', decision_authority=[], escalation_conditions=[], criteria_it_may_judge=[]))])
def test_malformed_mandate_is_explicitly_rejected(body):
    with pytest.raises(ValueError, match='invalid mandate'):
        Mandate.parse(body)


def test_all_mandate_fields_are_preserved():
    fields = dict(goal='Ship', constraints=['No deploy'], decision_authority=['Plan'],
                  escalation_conditions=['Risk'], criteria_it_may_judge=['c1'])
    mandate = Mandate.parse(json.dumps(fields))
    assert vars(mandate) == fields
