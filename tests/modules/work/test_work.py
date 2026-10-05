from contextlib import contextmanager
from datetime import datetime, timedelta, timezone

import pytest

from fleet.modules.work import WorkFacade, Evidence, EvidenceSpecification


class MemoryRepository:
    def __init__(self):
        self.records = {}
        self.history = []

    @contextmanager
    def transaction(self):
        yield self

    def get(self, kind, identity):
        return self.records[kind, identity]

    def list(self, kind):
        return [record for (category, _), record in self.records.items() if category == kind]

    def save(self, kind, record, actor):
        self.records[kind, record.id] = record
        self.history.append((kind, record, actor))


class EvidenceReader:
    def get(self, reference):
        return {"test:pass": Evidence("test:pass", "passed"),
                "test:fail": Evidence("test:fail", "failed")}.get(reference)

    def check(self, reference):
        if reference.startswith("page:"):
            raise ValueError("unreadable evidence reference")


@pytest.fixture
def work():
    now = [datetime(2026, 9, 27, tzinfo=timezone.utc)]
    repository = MemoryRepository()
    return WorkFacade(repository, EvidenceReader(), lambda: now[0]), repository, now


def add(work, **fields):
    return work.add(project="p1", title="Work", goal="Deliver", actor="user", **fields)


@pytest.mark.parametrize("verification", ["checked", "judged", "accepted"])
def test_criteria_require_actor_and_checked_evidence(work, verification):
    facade, repo, _ = work
    item = add(facade)
    spec = EvidenceSpecification("test:pass", "passed") if verification == "checked" else None
    criterion = facade.add_criterion(item.id, text="Works", verification=verification,
                                     specification=spec, actor="user")
    before = len(repo.history)
    with pytest.raises(ValueError, match="actor"):
        facade.meet(criterion.id, actor="", evidence=("test:pass",))
    if verification == "checked":
        for references in [(), ("missing",), ("test:fail",)]:
            with pytest.raises(ValueError, match="evidence"):
                facade.meet(criterion.id, actor="user", evidence=references)
    assert len(repo.history) == before
    result = facade.meet(criterion.id, actor="reviewer", evidence=("test:pass",))
    assert (result.state, result.met_by, result.met_at) == ("met", "reviewer", facade.clock())


def test_required_evidence_result_is_checked(work):
    facade, _, _ = work
    item = add(facade)
    criterion = facade.add_criterion(item.id, text="Pass", verification="checked",
        specification=EvidenceSpecification("test:fail", "passed"), actor="user")
    with pytest.raises(ValueError, match="evidence"):
        facade.meet(criterion.id, actor="user", evidence=("test:fail",))


def test_named_but_unrecorded_evidence_is_not_enough(work):
    facade, _, _ = work
    item = add(facade)
    criterion = facade.add_criterion(item.id, text="Exists", verification="checked",
        specification=EvidenceSpecification("missing"), actor="user")
    with pytest.raises(ValueError, match="evidence"):
        facade.meet(criterion.id, actor="user", evidence=("missing",))


def test_unreadable_evidence_reference_is_rejected_when_added(work):
    facade, repo, _ = work
    item = add(facade)
    with pytest.raises(ValueError, match="unreadable"):
        facade.add_criterion(item.id, text="Replied", verification="checked",
                             specification=EvidenceSpecification("page:p/notes"), actor="user")
    assert facade.criteria(item.id) == []


def test_withdrawn_criteria_stop_counting_but_stay_on_record(work):
    facade, _, _ = work
    item = add(facade)
    met = facade.add_criterion(item.id, text="Works", verification="judged", actor="user")
    stale = facade.add_criterion(item.id, text="Unmeetable", verification="judged", actor="user")
    facade.meet(met.id, actor="user")
    assert (facade.progress(item.id).complete, facade.progress(item.id).total) == (1, 2)
    with pytest.raises(ValueError, match="reason"):
        facade.withdraw(stale.id, actor="user", reason="")
    withdrawn = facade.withdraw(stale.id, actor="user", reason="evidence can never be recorded")
    assert (withdrawn.state, withdrawn.withdrawn_by, withdrawn.withdrawn_at) == ("withdrawn", "user", facade.clock())
    assert (facade.progress(item.id).complete, facade.progress(item.id).total) == (1, 1)
    assert len(facade.criteria(item.id)) == 2
    for identity in (met.id, stale.id):
        with pytest.raises(ValueError, match="only an unmet"):
            facade.withdraw(identity, actor="user", reason="again")
    with pytest.raises(ValueError, match="withdrawn"):
        facade.meet(stale.id, actor="user")


def test_arbitrary_nesting_moves_and_project_labels(work):
    facade, _, _ = work
    epic = add(facade, kind="epic", focus="priority")
    parent = epic
    for _ in range(4):
        parent = add(facade, parent=parent.id)
    direct = add(facade, parent=epic.id)
    assert facade.get(parent.id).parent != epic.id
    assert direct.parent == epic.id
    assert facade.move(parent.id, parent=epic.id, actor="user").parent == epic.id
    with pytest.raises(ValueError, match="cycle"):
        facade.move(epic.id, parent=direct.id, actor="user")
    custom = add(facade, kind="investigation")
    assert "investigation" in facade.kinds("p1")
    assert custom.kind == "investigation"


def test_focus_only_epics_and_waiting_is_manual(work):
    facade, repo, now = work
    with pytest.raises(ValueError, match="epic"):
        add(facade, focus="priority")
    item = add(facade)
    with pytest.raises(ValueError, match="epic"):
        facade.set(item.id, focus="background", actor="user")
    with pytest.raises(ValueError, match="resume"):
        facade.set(item.id, condition="waiting", actor="user")
    facade.set(item.id, condition="waiting", resume_condition="Data arrives", actor="user")
    count = len(repo.history)
    now[0] += timedelta(days=10)
    assert facade.get(item.id).condition == "waiting"
    assert len(repo.history) == count
    assert facade.ready(item.id, actor="user").condition == "ready for review"
    with pytest.raises(ValueError, match="waiting"):
        facade.ready(item.id, actor="user")
