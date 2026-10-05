"""Asynchronous page replies using the existing triage authority and records."""
from __future__ import annotations

import json
import logging
import queue
import threading

from collections.abc import Callable

from fleet.errors import FleetError
from fleet.modules.authority import AuthorityRejected
from fleet.modules.execution import Run, Usage
from fleet.services.facades import Facades
from fleet.services.page_requests import request_token
from fleet.services.reply_stream import ReplyStream
from fleet.services.responder import (
    AppServerPort,
    ResponderWorker,
    TurnResult,
    turn_health,
)
from fleet.triage import TriageCommands

REPLY_SCHEMA = {
    'type': 'object', 'properties': {
        'reply': {'type': 'string'}, 'escalate': {'type': 'boolean'}, 'reason': {'type': 'string'}},
    'required': ['reply', 'escalate', 'reason'], 'additionalProperties': False,
}


def parse_reply(text: str) -> dict:
    if len(text.encode()) > 65_536:
        raise ValueError('responder output exceeds 64 KiB')
    result = json.loads(text)
    if (not isinstance(result, dict) or set(result) != {'reply', 'escalate', 'reason'}
            or not isinstance(result['reply'], str) or type(result['escalate']) is not bool
            or not isinstance(result['reason'], str)):
        raise ValueError('responder output must contain reply:string, escalate:boolean, reason:string')
    if result['escalate'] and not result['reason'].strip():
        raise ValueError('responder escalation requires a nonempty reason')
    if len(result['reason'].encode()) > 8192:
        raise ValueError('responder escalation reason exceeds 8 KiB')
    if not result['escalate'] and (not result['reply'].strip() or len(result['reply'].encode()) > 8192):
        raise ValueError('responder reply must be nonempty and at most 8 KiB')
    return result


class PageResponder:
    def __init__(self, services: Facades, worker: ResponderWorker) -> None:
        self.services, self.worker = services, worker
        self.pending: queue.Queue[str] = queue.Queue()
        self.submitted: set[str] = set()
        self.lock = threading.Lock()
        self.processing = threading.Lock()
        self.server: AppServerPort | None = None
        self.threads: dict[str, str] = {}
        self.on_typing: Callable[[str, dict | None], None] = lambda item, value: None

    def submit(self, run: Run, *, reconcile: bool = False) -> None:
        if run.kind != 'responder':
            raise ValueError('page responder accepts only responder runs')
        with self.lock:
            if run.id not in self.submitted:
                self.submitted.add(run.id)
                self.pending.put(run.id)

    def run(self, stop: threading.Event, recovered: Callable[[str], None]) -> None:
        recovered('page-responder')
        while not stop.is_set():
            # Let the serve-owned process initialize before consuming the queue.
            # A reported startup failure is actionable unavailability, not a wait.
            health = self.worker.health()
            if not health.get('ready') and not health.get('error'):
                stop.wait(0.1)
                continue
            if not self.process_next():
                stop.wait(0.1)

    def process_next(self) -> bool:
        """One bounded attempt. Production has one consumer; tests can drive it explicitly."""
        with self.processing:
            try:
                identity = self.pending.get_nowait()
            except queue.Empty:
                return False
            try:
                self._process(identity)
            finally:
                with self.lock:
                    self.submitted.discard(identity)
                self.pending.task_done()
        return True

    @staticmethod
    def authorized(services: Facades, action, item) -> None:
        captured = action.payload['responder']
        if str(item.owner_at) != captured['owner_at']:
            raise AuthorityRejected('attention ownership changed since responder reservation')
        services.authority.require_triage('reply_attention', item, actor=action.actor, activation=action.activation)

    def _process(self, identity: str) -> None:
        run = self.services.execution.get_run(identity)
        if run.kind != 'responder' or run.status != 'running':
            return
        action = self.services.execution.get_action(run.action)
        captured = action.payload['responder']
        result = None
        stream = None
        try:
            item = self.services.attention.get(captured['item'])
            self.authorized(self.services, action, item)
            server = self.worker.client()
            if self.server is not server:
                self.server = server
                self.threads.clear()
            if item.id not in self.threads:
                self.threads[item.id] = server.start_thread()
            def publish(text: str) -> None:
                current = self.services.attention.get(item.id)
                try:
                    self.authorized(self.services, action, current)
                except AuthorityRejected:
                    self.on_typing(item.id, None)
                    return
                self.on_typing(item.id, dict(run=run.id, project=item.project,
                                            owner_at=item.owner_at.isoformat(), text=text,
                                            reply_ids=[reply.id for reply in item.replies]))
            stream = ReplyStream(publish)
            result = server.turn(self.threads[item.id], action.payload['steps'][0]['prompt'],
                                 output_schema=REPLY_SCHEMA, effort='low', on_delta=stream.delta)
            if result.status != 'completed':
                raise FleetError('responder turn ' + result.status)
            response = parse_reply(result.text)
            self._finish(run, action, result=result, reply=None if response['escalate'] else response['reply'],
                         fallback=response['reason'] if response['escalate'] else None)
        except AuthorityRejected as error:
            self._finish(run, action, result=result, stopped=str(error))
        except (FleetError, ValueError, LookupError, OSError, RuntimeError) as error:
            reason = str(error)
            if not reason.startswith('responder unavailable:'):
                reason = 'responder unavailable: ' + reason
            self._finish(run, action, result=result, fallback=reason, failed=True)
        finally:
            if stream is not None:
                stream.close()
            self.on_typing(captured['item'], None)

    def _finish(self, run: Run, action, *, result: TurnResult | None = None, reply: str | None = None,
                fallback: str | None = None, stopped: str | None = None, failed: bool = False) -> None:
        decision = None
        with self.services.triage_repository.transaction() as scope:
            services, repository = scope.services, scope.records
            if services.execution.get_run(run.id).status != 'running':
                return
            item = services.attention.get(action.payload['responder']['item'])
            try:
                self.authorized(services, action, item)
            except AuthorityRejected as error:
                stopped, reply, fallback = str(error), None, None
            state = repository.get(action.project)
            if state.get('run') != run.id:
                stopped, reply, fallback = 'responder reservation is no longer current', None, None
            if stopped is None and reply is not None:
                activation = services.authority.get(action.activation)
                accepted = TriageCommands(services, activation).execute('reply', {'item': item.id, 'body': reply})
                decision = accepted['decision']
                state.setdefault('handled', {})[item.id] = action.payload['responder']['request']
                state.setdefault('fallback', {}).pop(item.id, None)
            if fallback is not None:
                state.setdefault('fallback', {})[item.id] = {
                    'request': request_token(item), 'reason': fallback, 'source_run': run.id}
            services.execution.finish_responder(run.id, actor=action.actor,
                status='stopped' if stopped else 'failed' if failed else 'succeeded',
                reason=stopped or (('escalated to fleetd: ' + fallback) if fallback else None),
                timings=None if result is None else {key: value for key, value in turn_health(result).items()
                                                     if key != 'usage'},
                usage=None if result is None else Usage('codex-app-server', [result.usage]))
            repository.save(action.project, state)
        # Publication follows the atomic store commit. A publication failure must
        # not launch a second answer; the existing history follower reconciles it.
        if decision is not None:
            intent = next(intent for intent in self.services.records.intents() if intent['key'] == decision['id'])
            try:
                self.services.records.publish(intent, json.dumps(decision, default=str))
            except (FleetError, ValueError, LookupError, OSError, RuntimeError):
                logging.getLogger(__name__).exception('Responder reply recorded; Decision publication pending')
