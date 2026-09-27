from contextlib import contextmanager
from types import SimpleNamespace

import pytest

from fleet.modules.execution import ExecutionFacade


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
