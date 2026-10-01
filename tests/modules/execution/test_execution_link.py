from contextlib import contextmanager
from types import SimpleNamespace

import pytest

from fleet.modules.execution import ExecutionFacade, JobObservation


class Repository:
    def __init__(self):
        self.saved = []

    @contextmanager
    def transaction(self):
        yield self

    def find(self, host, job):
        return next((run for _, run, _ in self.saved if (run.host, run.remote_job_id) == (host, job)), None)

    def actions(self):
        return [action for action, _, _ in self.saved]

    def save(self, action, run, actor):
        self.saved.append((action, run, actor))

    def runs(self):
        return [run for _, run, _ in self.saved]

    def update(self, run, actor):
        self.saved = [(action, run if previous.id == run.id else previous, actor)
                      for action, previous, _ in self.saved]


def test_link_only_reads_work_and_deduplicates_by_host_and_job():
    reads = []
    work = SimpleNamespace(get=lambda identity: reads.append(identity))
    repository = Repository()
    execution = ExecutionFacade(repository, work)
    first = execution.link("one", "job", "work", actor="user")
    assert execution.link("one", "job", "work", actor="user") == first
    second = execution.link("two", "job", "work", actor="user")
    assert first.id != second.id
    assert len(repository.saved) == 2
    assert reads == ["work", "work", "work"]
    with pytest.raises(ValueError, match="actor"):
        execution.link("one", "new", "work", actor="")


def test_stalled_runner_is_not_confirmed_lost_and_reconciliation_does_not_call_work():
    reads = []
    work = SimpleNamespace(get=lambda identity: reads.append(identity))
    execution = ExecutionFacade(Repository(), work)
    linked = execution.link("host", "job", "work", actor="user")
    stalled = execution.observe("host", JobObservation("job", "stalled", "codex", None, None, None))
    assert stalled.id == linked.id
    assert stalled.status == "unknown outcome" and stalled.reason == "stalled"
    assert reads == ["work"]
    assert execution.observe("other", JobObservation("job", "failed", "codex", None, None, None)) is None
    assert execution.runs() == [stalled]
