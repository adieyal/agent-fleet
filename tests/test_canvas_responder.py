import json
from types import SimpleNamespace

import pytest

from fleet.modules.canvas.application.orchestrator import deterministic
from fleet.services.canvas_responder import answer


@pytest.mark.parametrize("command", ["park everything", "what's costing the most?",
                                      "create an epic for migration", "show me swimlanes"])
def test_deterministic_commands(command):
    assert deterministic(command)


def test_question_routes_to_agent():
    assert not deterministic("are the inbox items prioritised?")


class Canvas:
    def __init__(self):
        self.message = {"who": "orchestrator · codex", "proposals": []}
        self.updates = []

    def state(self, space, *, person):
        assert space == "project" and person == "user"
        return {"items": [{"title": "Inbox task", "band": None}], "bands": ["now", "next"],
                "regions": [], "runs": [], "attention": []}

    def kernel_step(self, space, change):
        assert space == "project"
        def need(kind, identity, label):
            assert (kind, identity, label) == ("message", "reply", "message")
            return self.message
        change(SimpleNamespace(need=need))
        self.updates.append(dict(self.message))


def test_context_stream_and_read_only_output():
    canvas = Canvas()
    class Server:
        def start_thread(self):
            return "thread"

        def turn(self, thread, prompt, *, output_schema, effort, on_delta):
            assert thread == "thread"
            context = json.loads(prompt.split("\n", 1)[1])
            assert context["question"] == "are the inbox items prioritised?"
            assert context["space"] == canvas.state("project", person="user")
            assert output_schema["additionalProperties"] is False
            on_delta('{"reply":"Inbox task has no band')
            return SimpleNamespace(status="completed", text='{"reply":"Inbox task has no band assigned."}')
    worker = SimpleNamespace(client=lambda: Server())
    changes = []
    answer(canvas, worker, "project", "reply", "are the inbox items prioritised?", lambda: changes.append(True))
    assert canvas.updates[0]["status"] == "streaming"
    assert canvas.message["status"] == "completed"
    assert canvas.message["text"] == "Inbox task has no band assigned."
    assert canvas.message["proposals"] == []
    assert len(changes) == 2


@pytest.mark.parametrize("worker", [None, SimpleNamespace(client=lambda: BadServer())])
def test_unavailable_is_visible(worker):
    canvas = Canvas()
    answer(canvas, worker, "project", "reply", "question", lambda: None)
    assert canvas.message["status"] == "failed"
    assert canvas.message["text"].startswith("Responder unavailable:")
    assert canvas.message["proposals"] == []


class BadServer:
    def start_thread(self):
        raise RuntimeError("app-server stopped")

    def turn(self, *args, **kwargs):
        raise AssertionError("unavailable server must not receive a turn")
