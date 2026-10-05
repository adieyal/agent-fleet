"""Bounded project triage scheduling, independent of transport and browser state."""
from __future__ import annotations

import os
from datetime import datetime, timedelta
from typing import Callable, TYPE_CHECKING

from fleet.transport import Host
from fleet.modules.execution import Run

if TYPE_CHECKING:
    from fleet.services.facades import Facades

from fleet.errors import FleetError
from fleet.modules.records import TRIAGE_PATH
from fleet.triage import triage_prompt


class TriageScheduler:
    def __init__(self, services: Facades, deliver: Callable[..., object] | None,
                 host: Callable[[str], Host] | None) -> None:
        self.services, self.deliver, self.host = services, deliver, host

    @staticmethod
    def queue(services, project: str):
        return [item for item in services.attention.list(project=project, owner='agent')
                if item.state in ('open', 'acknowledged')]

    def status(self, project: str) -> dict:
        state = self.services.triage_repository.get(project)
        policy_error = None
        try:
            mandate = self.services.records.triage_mandate(project)
            version = None if mandate is None else self.services.records.mandate_version(project, TRIAGE_PATH)[0]
        except (FleetError, ValueError, LookupError, OSError) as error:
            mandate = version = None
            policy_error = str(error)
        used = state.get('used', 0) if state.get('day') == self.services.store.clock().date().isoformat() else 0
        run = state.get('run')
        now = self.services.store.clock()
        reset = (now + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
        queued = self.queue(self.services, project)
        starts = [datetime.fromisoformat(state['waiting'][i.id])
                  if i.id in state.get('waiting', {}) else i.owner_at for i in queued]
        oldest = None if not starts or any(start is None for start in starts) else min(starts)
        return dict(project=project, mandate_version=version, policy_error=policy_error,
                    queue=[i.id for i in queued],
                    live_run=None if run is None else dict(id=run, status=self.services.execution.get_run(run).status),
                    budget_left=None if mandate is None else max(0, mandate.limits['runs_per_day'] - used),
                    budget_resets_at=None if mandate is None else reset.isoformat(),
                    oldest_wait_seconds=None if oldest is None else max(0, (now - oldest).total_seconds()),
                    delivery_error=state.get('error'),
                    pending_publications=[intent for intent in self.services.records.intents()
                                          if intent['project'] == project and intent['state'] == 'pending'])

    def schedule(self) -> None:
        projects = {i.project for i in self.services.attention.list(owner='agent') if i.state != 'resolved'}
        projects.update(self.services.triage_repository.projects())
        # A reservation must be reconciled even after every item was taken back or resolved.
        projects.update(a.project for a in self.services.execution.actions() if a.activation and
                        self.services.authority.get(a.activation).role == 'triage')
        for project in sorted(projects):
            try:
                delivery = self.reserve(project)
                self.services.decisions.publish_triage_guards(project)
            except (FleetError, ValueError, LookupError, OSError, RuntimeError) as error:
                self.delivery_error(project, str(error))
                continue
            if delivery is None:
                if self.services.triage_repository.get(project).get('run') is None:
                    self.delivery_error(project, None)
                continue
            run, reconcile = delivery
            try:
                self.deliver(run, reconcile=reconcile)
            except (FleetError, ValueError, LookupError, OSError, RuntimeError) as error:
                self.delivery_error(project, str(error))
            else:
                self.delivery_error(project, None)

    def delivery_error(self, project: str, error: str | None) -> None:
        with self.services.triage_repository.transaction() as scope:
            repository = scope.records
            state = repository.get(project)
            state['error'] = error
            repository.save(project, state)

    def reserve(self, project: str) -> tuple[Run, bool] | None:
        now = self.services.store.clock()
        with self.services.triage_repository.transaction() as scope:
            repository = scope.records
            services = scope.services
            state = repository.get(project)
            mandate = services.records.triage_mandate(project)
            if mandate is None:
                return None
            version = services.records.mandate_version(project, TRIAGE_PATH)[0]
            def escalate(item, reason: str) -> None:
                recovery = (f' Inspect fleet triage policy show {project} and fleet triage status {project}. '
                            'Decide whether to handle this item yourself or restore service and delegate it again.')
                if 'outcome unknown' in reason:
                    recovery += (f' Inspect fleet run show {state.get("run")}; '
                                 'do not launch a duplicate while the outcome is unknown.')
                services.decisions.escalate_triage_guard(item.id, reason=reason + recovery, mandate_version=version,
                                                        source_run=state.get('run'))
            items = self.queue(services, project)
            if state.get('day') != now.date().isoformat():
                state.update(day=now.date().isoformat(), used=0)
            waiting = state.setdefault('waiting', {})
            untouched = state.setdefault('untouched', {})
            for item in items:
                if item.id not in waiting:
                    waiting[item.id] = (item.owner_at or now).isoformat()
                elif item.owner_at is not None and item.owner_at > datetime.fromisoformat(waiting[item.id]):
                    waiting[item.id] = item.owner_at.isoformat()
                    untouched.pop(item.id, None)  # A new delegation starts a fresh bounded attempt.

            if state.get('run'):
                run = services.execution.get_run(state['run'])
                if run.status in ('running', 'unknown outcome'):
                    for item in items:
                        if (item.id not in state.get('items', []) and
                                now - datetime.fromisoformat(waiting[item.id]) >=
                                timedelta(minutes=mandate.limits['unclaimed_minutes'])):
                            escalate(item, f'triage item remained unclaimed: project run {run.id} is still {run.status}')
                    items = self.queue(services, project)
                    if run.status == 'unknown outcome' and state.get('error'):
                        deadline = datetime.fromisoformat(state['claimed_at']) + timedelta(minutes=mandate.limits['unclaimed_minutes'])
                        if now >= deadline:
                            for item in items:
                                escalate(item, f'triage host unreachable or dispatch unconfirmed: {state["error"]}; run {run.id} outcome unknown')
                    repository.save(project, state)
                    # Retry transport at most once a minute; never create another run for an unknown outcome.
                    last = state.get('delivery_at')
                    if run.status == 'unknown outcome' and (last is None or now - datetime.fromisoformat(last) >= timedelta(minutes=1)):
                        state['delivery_at'] = now.isoformat()
                        repository.save(project, state)
                        return run, True
                    return None
                acted = {d.attention_item for d in services.decisions.list() if d.source_run == run.id}
                for item in items:
                    if item.id not in state.get('items', []) or item.id in acted:
                        continue
                    untouched[item.id] = untouched.get(item.id, 0) + 1
                    if untouched[item.id] >= 2:
                        escalate(item, f'triage run {run.id} ended without acting (second untouched run)')
                for identity in state.get('items', []):
                    waiting[identity] = now.isoformat()
                state.update(run=None, items=[])
                items = self.queue(services, project)
            # Include activations launched by another controller before scheduler state existed.
            if not state.get('run'):
                for candidate in services.execution.runs():
                    action = services.execution.get_action(candidate.action)
                    if (action.project == project and action.activation and
                            services.authority.get(action.activation).role == 'triage' and
                            candidate.status in ('running', 'unknown outcome')):
                        state.update(run=candidate.id, items=[], claimed_at=now.isoformat(),
                                     used=state['used'] + 1, delivery_at=now.isoformat())
                        repository.save(project, state)
                        return None
            for item in list(items):
                if now - datetime.fromisoformat(waiting[item.id]) >= timedelta(minutes=mandate.limits['unclaimed_minutes']):
                    escalate(item, 'triage item was not claimed before its timeout; fleet web was down or dispatch was unavailable')
                    items.remove(item)
            if state['used'] >= mandate.limits['runs_per_day']:
                for item in items:
                    escalate(item, f'triage daily budget exhausted ({state["used"]}/{mandate.limits["runs_per_day"]} runs); resets at {(now + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0).isoformat()}')
                items = []
            if not items:
                repository.save(project, state)
                return None
            host = self.host(mandate.host)
            if not host.is_local:
                raise ValueError('triage must run on the controller machine')
            activation = services.authority.activate(project=project, actor='triage',
                role='triage', mandate_path=TRIAGE_PATH)
            label = services.workspace.host_label(project, mandate.host)
            arguments = ['create', '--project', label, '--description', f'Triage: handling {len(items)} items',
                         '--agent', mandate.runtime, '--cwd', mandate.cwd, '--permission', mandate.permission,
                         '--steps-file', '/dev/stdin', '--hold',
                         '--add-dir', str(services.store.path.parent),
                         '--add-dir', services.workspace.management_repository(project)]
            for name in ('FLEET_STORE', 'FLEET_CONFIG', 'FLEET_HOME', 'FLEET_MANAGEMENT'):
                if name in os.environ:
                    arguments += ['--env', name + '=' + os.environ[name]]
            prompt = triage_prompt(activation, mandate, [i.id for i in items])
            guidance_metadata = None
            constitution = services.records.guidance(project)
            if constitution is not None:
                guidance_metadata = dict(project=project, epic=None, charter=None,
                    constitution=dict(path=constitution.path, revision=constitution.version.revision,
                                      version=constitution.version.number))
                prompt += '\nProject constitution (' + constitution.version.revision + '):\n' + constitution.body
            for work in sorted({i.work_item for i in items if i.work_item is not None}):
                guidance = services.records.dispatch_guidance(work)
                if guidance and guidance['charter']:
                    entry = guidance['charter']
                    prompt += f'\nCharter for {work} ({entry["revision"]}):\n' + services.records.read(
                        project, entry['path'], revision=entry['revision'])
            run = services.execution.dispatch(None, project=project, host=mandate.host, runtime=mandate.runtime,
                actor=activation.actor, activation=activation.id, guidance=guidance_metadata,
                reason=f'Triage: handling {len(items)} items',
                idempotency_key=f'triage:{project}:{items[0].id}:{activation.id}',
                payload=dict(arguments=arguments, cwd=mandate.cwd, steps=[dict(prompt=prompt, title='Triage')],
                             context=[], hold=False)).run
            state.update(run=run.id, items=[i.id for i in items], claimed_at=now.isoformat(),
                         delivery_at=now.isoformat(), used=state['used'] + 1, error=None)
            for item in items:
                waiting[item.id] = now.isoformat()
            repository.save(project, state)
            return run, False
