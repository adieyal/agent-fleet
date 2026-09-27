from dataclasses import FrozenInstanceError
from datetime import datetime, timezone

import pytest

from fleet.modules.decisions import Decision


def test_recorded_decision_cannot_be_edited():
    decision = Decision("d", "a", "Question?", "Yes", "adi", "doc:1", ("w",),
                        datetime(2026, 9, 27, tzinfo=timezone.utc))
    with pytest.raises(FrozenInstanceError):
        decision.answer = "No"
    with pytest.raises(TypeError):
        decision.affected_work_items[0] = "other"
