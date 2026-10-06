"""Public canvas kernel: load a space, apply one operation or tick, persist its records and log."""
from __future__ import annotations

from datetime import datetime
from typing import Callable

from .application.operations import Engine
from .application.ports import CanvasRepository, Ports
from .application.kernel import Refused
from .application.view import read_model

QUIET = ("reader.mark_seen", "decision.check", "note.dismiss")
NO_TICK = ("decision.check", "reader.mark_seen", "note.dismiss", "space.init", "tick", "message.send")


class CanvasFacade:
    def __init__(self, repository: CanvasRepository, clock: Callable[[], datetime]) -> None:
        self.repository, self.clock = repository, clock

    def engine(self, space: str, ports: Ports, *, actor: str, op_id: str | None = None) -> Engine:
        engine = Engine(space, self.repository.load(space), ports, now=self.clock(), actor=actor, op_id=op_id)
        engine.last_seq = self.repository.last_event(space)
        return engine

    def initialized(self, space: str) -> bool:
        return "main" in self.repository.load(space).get("workflow", {})

    def execute(self, space: str, ports: Ports, *, actor: str, op: str, args: dict, op_id: str | None = None) -> dict:
        """One operation in the caller's transaction. A Refused leaves nothing behind for the caller to commit."""
        if op_id is not None:
            previous = self.repository.op_result(op_id)
            if previous is not None:
                return previous | {"replayed": True}
        engine = self.engine(space, ports, actor=actor, op_id=op_id)
        if op != "space.init" and engine.get("workflow", "main") is None:
            raise Refused("not_found", f"project {space} has no canvas yet; run fleet canvas init {space}")
        result = engine.apply(op, args)
        if op not in NO_TICK and not (op == "proposal.resolve" and args.get("adopt") is False):
            engine.tick()
        sequence = self.commit(space, engine)
        body = {"ok": True, "op": op, "op_id": op_id, "result": result, "seq": sequence,
                "toasts": engine.toasts}
        if op_id is not None:
            self.repository.remember_op(op_id, space, op, body)
        return body

    def commit(self, space: str, engine: Engine) -> int:
        for kind, identity, record, presentation in engine.changes():
            if presentation:
                self.repository.present(space, kind, identity, record)
            else:
                self.repository.save(space, kind, identity, record, engine.actor)
        for kind, identity, version, record in engine.versions:
            self.repository.save_version(space, kind, identity, version, record)
        sequence = self.repository.last_event(space)
        for event in engine.events:
            sequence = self.repository.append_event(space, event)
        return sequence

    def refusal(self, space: str, refusal: Refused, *, actor: str, op: str, op_id: str | None) -> dict:
        """Write a refusal to the event log and remember it, so a retried gesture is refused the same way."""
        body = refusal.as_dict(op_id)
        source = refusal.source or {}
        label = f"{source.get('object')} v{source.get('version')}" if source.get("object") else (
            "you" if actor in ("user", "web-user") else actor)
        self.repository.append_event(space, {
            "time": self.clock().isoformat(), "actor": actor, "source": label, "line": source.get("line") or 0,
            "text": f"refused {op}: {refusal.message}", "tone": "refuse", "subject": None, "op_id": op_id})
        if op_id is not None and refusal.code != "version_conflict":
            self.repository.remember_op(op_id, space, op, body)
        return body

    def read(self, space: str, ports: Ports, *, person: str, project_name: str, other_attention: list,
             events: int = 200) -> dict:
        engine = self.engine(space, ports, actor=person)
        if engine.get("workflow", "main") is None:
            raise LookupError(f"project {space} has no canvas yet")
        last = self.repository.last_event(space)
        log = self.repository.events(space, after=max(0, last - events))
        seen = (engine.get("seen", person) or {}).get("seq", 0)
        since = self.repository.events(space, after=seen, limit=400) if seen < last - events else []
        model = read_model(engine, person=person, events=log, last_seq=last,
                           layout=self.repository.layout(space, person), project_name=project_name,
                           other_attention=other_attention)
        if since:
            model["since_log"] = since
        return model

    def versions(self, space: str, kind: str, identity: str) -> list[dict]:
        return self.repository.versions(space, kind, identity)

    def events(self, space: str, *, after: int = 0, limit: int | None = None) -> list[dict]:
        return self.repository.events(space, after=after, limit=limit)

    def set_layout(self, space: str, person: str, object_ref: str, props: dict | None) -> None:
        self.repository.set_layout(space, person, object_ref, props)

    def spaces(self) -> list[str]:
        return self.repository.spaces()

    def outbox(self, space: str) -> list[dict]:
        """Run requests waiting on the host side: dispatch, cancel or permit."""
        runs = self.repository.load(space).get("run", {})
        return [run for run in runs.values() if (run.get("dispatch") and not run.get("fleet_run") and run["state"] == "starting")
                or (run.get("cancel") and run.get("fleet_run")) or (run.get("permit") and not run.get("permitted"))]
