"""Prepare and deliver guided dispatch and orchestration commands."""
from __future__ import annotations

import json
import os
from uuid import uuid4

from fleet.api import DispatchRequest
from fleet.orchestration import guide, orchestrator_prompt
from fleet.transport import FleetError
from fleet.services.permission_profiles import dispatch_rules


class Dispatch:
    def __init__(self, services, transport, workspace, references, context, controller):
        self.services, self.transport = services, transport
        self.workspace, self.references, self.context = workspace, references, context
        self.controller = controller

    def prepare(self, arguments: DispatchRequest, steps: list[dict]):
        rules = dispatch_rules(arguments.allow_profile, arguments.allow, agent=arguments.agent)
        if arguments.work_item is not None:
            try:
                self.services.work.get(arguments.work_item)
            except LookupError as error:
                raise FleetError(str(error)) from error
        host = self.transport.host_by_name(arguments.host)
        workspace = self.workspace()
        project_id = workspace.resolve_project(arguments.project)
        label = workspace.host_label(project_id, host.name)
        if arguments.work_item is not None and self.services.work.get(arguments.work_item).project != project_id:
            raise FleetError("work item belongs to another project; use its registered project")
        self.references.step_work(steps)
        if not steps:
            raise FleetError("give at least one --step or a --steps-file")
        fleetd_arguments = ["create", "--project", label, "--description", arguments.description,
                            "--agent", arguments.agent, "--cwd", arguments.cwd,
                            "--steps-file", "/dev/stdin", "--hold"]
        if arguments.permission is not None:
            fleetd_arguments += ['--permission', arguments.permission]
        for flag, value in (("--model", arguments.model), ("--id", arguments.id)):
            if value:
                fleetd_arguments += [flag, value]
        if rules:
            fleetd_arguments += ["--allowed-tools", json.dumps(rules)]
        for directory in arguments.add_dir or []:
            fleetd_arguments += ["--add-dir", directory]
        for pair in arguments.env or []:
            if "=" not in pair:
                raise FleetError(f"--env takes NAME=value, not {pair}")
            fleetd_arguments += ["--env", pair]
        if arguments.keep_going:
            fleetd_arguments.append("--keep-going")
        execution = self.services.execution
        key = str(uuid4()) if arguments.id is None else arguments.id
        try:
            payload, guidance = guide(self.services.records, arguments.work_item,
                {"cwd": arguments.cwd, "arguments": fleetd_arguments, "steps": steps,
                 "context": arguments.context, "hold": arguments.hold})
            intent = execution.dispatch(arguments.work_item, host=host.name, runtime=arguments.agent,
                payload=payload, project=project_id, guidance=guidance,
                actor=arguments.actor, reason=arguments.description, idempotency_key=key,
                remote_job_id=arguments.id)
        except (ValueError, LookupError) as error:
            raise FleetError(str(error)) from error
        return intent, guidance

    def deliver(self, run, *, reconcile: bool = False) -> dict:
        host = self.transport.host_by_name(run.host)
        return self.services.execution.deliver(run,
            lambda arguments, stdin: self.transport.call(host, arguments, stdin_text=stdin),
            lambda job, context, guidance: self.context.push_guided(host, job, context, guidance), reconcile=reconcile)

    def send(self, request: DispatchRequest, steps: list[dict]) -> dict:
        intent, guidance = self.prepare(request, steps)
        if not intent.created:
            if intent.run.status == "unknown outcome":
                self.deliver(intent.run, reconcile=True)
            return dict(intent=intent, guidance=guidance, current=self.services.execution.get_run(intent.run.id), job=None)
        return dict(intent=intent, guidance=guidance, current=None, job=self.deliver(intent.run))

    def orchestrate(self, *, host, work_item, mandate, agent, cwd, permission):
        host = self.transport.host_by_name(host)
        if not host.is_local:
            raise FleetError('orchestrator must run on the controller machine')
        work_item = self.references.work(work_item)
        try:
            activation = self.services.authority.activate(work_item, actor='orchestrator',
                role='orchestrator', mandate_path=mandate)
            records = self.services.records
            _, mandate = records.mandate_version(activation.project, activation.mandate_path,
                                                 revision=activation.mandate_version)
            payload, guidance = guide(records, activation.work_item, dict(
                steps=[dict(prompt=orchestrator_prompt(activation, mandate), title='Orchestrate')], context=None))
            worker =['create', '--project', self.workspace().host_label(activation.project, host.name), '--description', 'Orchestrate work item',
                      '--agent', agent, '--cwd', cwd, '--steps-file', '/dev/stdin', '--hold']
            if permission is not None:
                worker += ['--permission', permission]
            for name in ('FLEET_STORE', 'FLEET_CONFIG', 'FLEET_HOME'):
                if name in os.environ:
                    worker += ['--env', name + '=' + os.environ[name]]
            intent = self.services.execution.dispatch(activation.work_item, actor=activation.actor,
                activation=activation.id, host=host.name, runtime=agent, guidance=guidance,
                payload=dict(payload, cwd=cwd, arguments=worker, hold=False),
                reason='Orchestrate work item', idempotency_key=activation.id)
            self.deliver(intent.run)
        except (ValueError, LookupError) as error:
            raise FleetError(str(error)) from error
        return activation, intent

    def control(self, activation: str, operation: str, payload: dict):
        try:
            result = self.controller(activation).execute(operation, payload)
            if operation == 'dispatch':
                self.deliver(result.run, reconcile=not result.created)
            elif operation == 'retry' and isinstance(result, dict) and result.get('run') is not None:
                self.deliver(self.services.execution.get_run(result['run']), reconcile=not result['created'])
            return result
        except (ValueError, LookupError, TypeError) as error:
            raise FleetError(str(error)) from error

    def retry(self, run: str, *, actor: str):
        execution = self.services.execution
        try:
            intent = execution.retry(run, actor=actor, idempotency_key=str(uuid4()))
        except (ValueError, LookupError) as error:
            raise FleetError(str(error)) from error
        if intent.created:
            self.deliver(intent.run)
        return intent.run

    def project_for_work(self, reference: str) -> str:
        try:
            return self.services.work.get(reference).project
        except LookupError as error:
            raise FleetError(str(error)) from error
