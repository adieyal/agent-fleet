"""fleet — send tasks to Claude Code / Codex agents on other machines and watch them work."""
from __future__ import annotations

import argparse
import json
import os
import re
import shlex
import subprocess
import sys
import time
from collections import defaultdict
from dataclasses import asdict
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

from rich.console import Console, Group
from rich.live import Live
from rich.markup import escape
from rich.text import Text
from rich.tree import Tree

from fleet import transport
from fleet.modules import workspace as projects
from fleet.composition import open_attention, open_decisions, open_execution, open_library, open_records, open_store, open_work, open_workspace
from fleet.projections.project import project_status
from fleet.modules.work import EvidenceSpecification
from fleet.modules.execution import Run
from fleet.transport import FleetError, Host, HostReport
from fleet.orchestration import ControllerCommands, orchestrator_prompt
from fleet.composition import open_authority
from fleet.web.server import serve, serve_fixture

console = Console()
error_console = Console(stderr=True)

STATUS_STYLE = {
    "running": ("●", "bold green"), "queued": ("◌", "yellow"), "stalled": ("◍", "magenta"),
    "failed": ("✗", "bold red"), "done": ("✓", "dim green"), "cancelled": ("⊘", "dim"),
}
STEP_STYLE = {
    "running": ("▶", "bold green"), "pending": ("○", "dim"), "done": ("✓", "green"),
    "failed": ("✗", "red"), "cancelled": ("⊘", "dim"),
}
TODO_STYLE = {"in_progress": ("▸", "cyan"), "pending": ("·", "dim"), "completed": ("✓", "dim green")}
TOOL_ICON = {"bash": "$", "edit": "✎", "read": "📖", "search": "🔍", "web": "🌐", "think": "💭",
             "delegate": "👥", "plan": "📝", "other": "⚙"}
LIST_ITEM = re.compile(r"^\s*(?:[-*]\s+(?:\[[ xX]\]\s+)?|\d+[.)]\s+)(.+)$")


# ------------------------------------------------------------- job refs


def resolve(reference: str) -> tuple[Host, str]:
    """Accept `host:id` or a bare id (searched on every host)."""
    if ":" in reference:
        host_name, job_id = reference.split(":", 1)
        host = transport.host_by_name(host_name)
        transport.ensure_master(host)
        return host, job_id
    matches = [(report.host, job["id"]) for report in transport.gather(transport.configured_hosts(), ["ls", "--all"])
               for job in report.jobs if job["id"].startswith(reference)]
    if len(matches) != 1:
        raise FleetError(f"'{reference}' matches {len(matches)} jobs; use host:id")
    return matches[0]


def age(timestamp: float | None) -> str:
    if not timestamp:
        return ""
    seconds = int(time.time() - timestamp)
    for unit, size in (("d", 86400), ("h", 3600), ("m", 60)):
        if seconds >= size:
            return f"{seconds // size}{unit}"
    return f"{seconds}s"


# ------------------------------------------------------------ rendering


def job_label(host_name: str, job: dict[str, Any]) -> Text:
    icon, style = STATUS_STYLE.get(job["status"], ("?", ""))
    steps = job["steps"]
    completed = sum(step["status"] == "done" for step in steps)
    label = Text()
    label.append(f"{icon} ", style)
    label.append(f"{host_name}:{job['id']}", "bold")
    label.append(f"  {job['agent']}", "cyan" if job["agent"] == "claude" else "blue")
    label.append(f"  {completed}/{len(steps)}", "bold" if completed < len(steps) else "dim")
    label.append(f"  {job['status']}", style)
    label.append(f"  {age(job.get('updated_at'))}", "dim")
    label.append(f"\n  {job['description']}")
    activity = job.get("activity")
    if activity and job["status"] == "running":
        tool_icon = TOOL_ICON.get(activity.get("tool", ""), "💬" if activity["kind"] == "text" else "!")
        label.append(f"\n  {tool_icon} {activity.get('summary', '')}", "italic dim" if activity["kind"] != "error" else "red")
    return label


def add_steps(node: Tree, job: dict[str, Any], *, brief: bool) -> None:
    steps = job["steps"]
    if brief and len(steps) <= 1:
        return
    for step in steps:
        icon, style = STEP_STYLE.get(step["status"], ("?", ""))
        line = Text(f"{icon} {step['index'] + 1}. {step['title']}", style)
        if step["status"] in ("done", "failed") and step.get("result") and not brief:
            line.append(f"  — {step['result'][:100]}", "dim")
        step_node = node.add(line)
        if step["status"] == "running" and job.get("todos"):
            for todo in job["todos"]:
                todo_icon, todo_style = TODO_STYLE.get(todo["status"], ("·", "dim"))
                step_node.add(Text(f"{todo_icon} {todo['text']}", todo_style))


SESSION_STYLE = {"working": ("●", "bold green"), "idle": ("◌", "yellow")}


def session_label(session: dict[str, Any]) -> Text:
    icon, style = SESSION_STYLE.get(session["status"], ("?", ""))
    label = Text()
    label.append(f"{icon} ", style)
    label.append(session["agent"], "cyan" if session["agent"] == "claude" else "blue")
    label.append(f"  {session.get('project') or '?'}", "bold")
    label.append(f"  {session['status']}", style)
    label.append(f"  {age(session.get('updated_at'))}", "dim")
    label.append(f"  {session.get('title') or session['id'][:8]}")
    activity = session.get("activity")
    if activity and session["status"] == "working":
        tool_icon = TOOL_ICON.get(activity.get("tool", ""), "💬" if activity["kind"] == "text" else "!")
        label.append(f"\n  {tool_icon} {activity.get('summary', '')}", "italic dim" if activity["kind"] != "error" else "red")
    return label


def render_sessions(sessions: dict[str, list[dict[str, Any]]]) -> list[Tree]:
    trees = []
    for host_name in sorted(sessions):
        if sessions[host_name]:
            tree = Tree(Text(f"live sessions · {host_name}", "bold underline"))
            for session in sorted(sessions[host_name], key=lambda session: -(session.get("updated_at") or 0)):
                tree.add(session_label(session))
            trees.append(tree)
    return trees


def render(reports: list[HostReport], *, group_by: str, brief: bool,
           sessions: dict[str, list[dict[str, Any]]] | None = None) -> Group:
    parts: list[Any] = []
    for report in reports:
        if report.error:
            parts.append(Text(f"⚠ {report.host.name}: {report.error}", "red"))
    groups: dict[str, list[tuple[str, dict[str, Any]]]] = defaultdict(list)
    for report in reports:
        for job in report.jobs:
            key = job["project"] if group_by == "project" else report.host.name
            groups[key].append((report.host.name, job))
    if not groups:
        parts.append(Text("no jobs", "dim"))
    for key in sorted(groups):
        jobs = groups[key]
        running = sum(job["status"] == "running" for _, job in jobs)
        tree = Tree(Text(f"{key}", "bold underline").append(f"  {running} running · {len(jobs)} jobs", "dim"))
        for host_name, job in sorted(jobs, key=lambda pair: (pair[1]["status"] != "running", -pair[1]["created_at"])):
            add_steps(tree.add(job_label(host_name, job)), job, brief=brief)
        parts.append(tree)
    parts.extend(render_sessions(sessions or {}))
    return Group(*parts)


def list_arguments(arguments: argparse.Namespace) -> list[str]:
    fleetd_arguments = ["ls", "--since-hours", str(arguments.since)]
    return fleetd_arguments + (["--all"] if arguments.all else [])


def selected_hosts(arguments: argparse.Namespace) -> list[Host]:
    hosts = transport.configured_hosts()
    if getattr(arguments, "host", None):
        hosts = [host for host in hosts if host.name in arguments.host]
    if not hosts:
        raise FleetError("no hosts configured — fleet host add <name> --ssh <target>")
    return hosts


def filter_reports(reports: list[HostReport], project: str | None) -> list[HostReport]:
    if project:
        for report in reports:
            report.jobs = [job for job in report.jobs if job["project"] == project]
    return reports


def gather_listing(hosts: list[Host], arguments: argparse.Namespace) -> tuple[list[HostReport], dict[str, list[dict[str, Any]]]]:
    """Jobs and live interactive sessions from every host, fetched side by side."""
    with ThreadPoolExecutor(max_workers=2) as pool:
        jobs = pool.submit(transport.gather, hosts, list_arguments(arguments))
        sessions = pool.submit(transport.gather_sessions, hosts) if arguments.sessions else None
        reports = filter_reports(jobs.result(), arguments.project)
        by_host = sessions.result() if sessions else {}
    if arguments.project:
        by_host = {name: [session for session in host_sessions if session.get("project") == arguments.project]
                   for name, host_sessions in by_host.items()}
    return reports, by_host


# ------------------------------------------------------------- commands


def command_list(arguments: argparse.Namespace) -> None:
    reports, sessions = gather_listing(selected_hosts(arguments), arguments)
    if arguments.json:
        print(json.dumps([{"host": report.host.name, "error": report.error, "jobs": report.jobs,
                           "sessions": sessions.get(report.host.name, [])} for report in reports]))
        return
    console.print(render(reports, group_by=arguments.group_by, brief=arguments.brief, sessions=sessions))


def command_watch(arguments: argparse.Namespace) -> None:
    hosts = selected_hosts(arguments)
    with Live(console=console, screen=True, auto_refresh=False) as live:
        while True:
            reports, sessions = gather_listing(hosts, arguments)
            header = Text(f"fleet · {time.strftime('%H:%M:%S')} · every {arguments.interval}s · ctrl-c to quit\n", "dim")
            live.update(Group(header, render(reports, group_by=arguments.group_by, brief=arguments.brief,
                                             sessions=sessions)), refresh=True)
            time.sleep(arguments.interval)


def read_steps(arguments: argparse.Namespace) -> list[dict[str, Any]]:
    steps: list[dict[str, Any]] = [{"prompt": step} for step in arguments.step or []]
    if arguments.steps_file:
        content = Path(arguments.steps_file).read_text()
        if arguments.steps_file.endswith(".json"):
            steps += [{"prompt": item} if isinstance(item, str) else item for item in json.loads(content)]
        else:
            items = [match.group(1).strip() for match in map(LIST_ITEM.match, content.splitlines()) if match]
            steps += [{"prompt": item} for item in items] or [{"prompt": content}]
    return steps


def push_context(host: Host, job_id: str, paths: list[str]) -> None:
    missing = [path for path in paths if not os.path.exists(path)]
    if missing:
        raise FleetError(f"context not found: {', '.join(missing)}")
    remote_directory = f"~/.fleet/jobs/{job_id}/context/" if not host.is_local else os.path.expanduser(
        f"~/.fleet/jobs/{job_id}/context/")
    transport.rsync([os.path.abspath(path) for path in paths], host.rsync_target(remote_directory), host)


def command_send(arguments: argparse.Namespace) -> None:
    command_dispatch(arguments)


def command_dispatch(arguments: argparse.Namespace) -> None:
    if arguments.work_item is not None:
        try:
            open_work().get(arguments.work_item)
        except LookupError as error:
            raise FleetError(str(error)) from error
    host = transport.host_by_name(arguments.host)
    workspace = open_workspace()
    linked_project = workspace.registry().project_for(host.name, arguments.project)
    project_id = linked_project.id if linked_project is not None else workspace.resolve_project(arguments.project)
    steps = read_steps(arguments)
    if not steps:
        raise FleetError("give at least one --step or a --steps-file")
    fleetd_arguments = ["create", "--project", arguments.project, "--description", arguments.description,
                        "--agent", arguments.agent, "--cwd", arguments.cwd,
                        "--steps-file", "/dev/stdin", "--hold"]
    if arguments.permission is not None:
        fleetd_arguments += ['--permission', arguments.permission]
    for flag, value in (("--model", arguments.model), ("--id", arguments.id)):
        if value:
            fleetd_arguments += [flag, value]
    if arguments.allow:
        fleetd_arguments += ["--allowed-tools", json.dumps(arguments.allow)]
    for directory in arguments.add_dir or []:
        fleetd_arguments += ["--add-dir", directory]
    for pair in arguments.env or []:
        if "=" not in pair:
            raise FleetError(f"--env takes NAME=value, not {pair}")
        fleetd_arguments += ["--env", pair]
    if arguments.keep_going:
        fleetd_arguments.append("--keep-going")
    from uuid import uuid4

    execution = open_execution()
    key = str(uuid4()) if arguments.id is None else arguments.id
    try:
        intent = execution.dispatch(arguments.work_item, host=host.name, runtime=arguments.agent,
            payload={"cwd": arguments.cwd, "arguments": fleetd_arguments, "steps": steps,
                     "context": arguments.context, "hold": arguments.hold}, project=project_id,
            actor="user", reason=arguments.description, idempotency_key=key, remote_job_id=arguments.id)
    except (ValueError, LookupError) as error:
        raise FleetError(str(error)) from error
    if not intent.created:
        if intent.run.status == "unknown outcome":
            deliver_dispatch(intent.run, reconcile=True)
        current = execution.get_run(intent.run.id)
        print(json.dumps({"job": f"{intent.run.host}:{intent.run.remote_job_id}", "status": current.status,
                          "run": intent.run.id, "action": intent.run.action, "steps": len(steps)}))
        return
    job = deliver_dispatch(intent.run)
    reference = f"{host.name}:{job['id']}"
    if arguments.json:
        print(json.dumps({"job": reference, "status": job["status"], "steps": len(job["steps"])}))
    else:
        console.print(f"[bold]{reference}[/] {job['status']} · {len(job['steps'])} step(s) · {job['description']}")
    if arguments.wait:
        wait_for([reference], step=None, timeout=None, as_json=arguments.json)


def deliver_dispatch(run: Run, *, reconcile: bool = False) -> dict:
    host = transport.host_by_name(run.host)
    return open_execution().deliver(run,
        lambda arguments, stdin: transport.call(host, arguments, stdin_text=stdin),
        lambda job, context: push_context(host, job, context), reconcile=reconcile)


def command_orchestrate(arguments: argparse.Namespace) -> None:
    host = transport.host_by_name(arguments.host)
    if not host.is_local:
        raise FleetError('orchestrator must run on the controller machine')
    store = open_store()
    try:
        activation = open_authority(store).activate(arguments.work_item, actor='orchestrator',
            role='orchestrator', mandate_path=arguments.mandate)
        records = open_records(store)
        _, mandate = records.mandate_version(activation.project, activation.mandate_path,
                                             revision=activation.mandate_version)
        prompt = orchestrator_prompt(activation, mandate)
        worker = ['create', '--project', activation.project, '--description', 'Orchestrate work item',
                  '--agent', arguments.agent, '--cwd', arguments.cwd, '--steps-file', '/dev/stdin', '--hold']
        if arguments.permission is not None:
            worker += ['--permission', arguments.permission]
        for name in ('FLEET_STORE', 'FLEET_CONFIG', 'FLEET_HOME'):
            if name in os.environ:
                worker += ['--env', name + '=' + os.environ[name]]
        intent = open_execution(store).dispatch(activation.work_item, actor=activation.actor,
            activation=activation.id, host=host.name, runtime=arguments.agent,
            payload=dict(cwd=arguments.cwd, arguments=worker, steps=[dict(prompt=prompt, title='Orchestrate')],
                         context=None, hold=False), reason='Orchestrate work item', idempotency_key=activation.id)
        deliver_dispatch(intent.run)
    except (ValueError, LookupError) as error:
        raise FleetError(str(error)) from error
    print(json.dumps(dict(activation=activation.id, run=intent.run.id, mandate_version=activation.mandate_version)))


def command_control(arguments: argparse.Namespace) -> None:
    try:
        result = ControllerCommands(open_store(), arguments.activation).execute(arguments.operation,
                                                                              json.loads(arguments.payload))
        if arguments.operation == 'dispatch':
            deliver_dispatch(result.run, reconcile=not result.created)
        print(json.dumps(result if isinstance(result, dict) else asdict(result), default=str))
    except (ValueError, LookupError, TypeError) as error:
        raise FleetError(str(error)) from error


def command_run_retry(arguments: argparse.Namespace) -> None:
    from uuid import uuid4

    execution = open_execution()
    try:
        intent = execution.retry(arguments.run, actor="user", idempotency_key=str(uuid4()))
    except (ValueError, LookupError) as error:
        raise FleetError(str(error)) from error
    if intent.created:
        deliver_dispatch(intent.run)
    print(json.dumps(asdict(intent.run), default=str))


def command_dispatch_work(arguments: argparse.Namespace) -> None:
    try:
        item = open_work().get(arguments.work_item)
    except LookupError as error:
        raise FleetError(str(error)) from error
    arguments.project = item.project
    arguments.description = arguments.instruction
    arguments.step = [arguments.instruction]
    command_dispatch(arguments)


def command_resolve_unknown(arguments: argparse.Namespace) -> None:
    try:
        run = open_execution().resolve_unknown(arguments.run, actor="user")
    except (ValueError, LookupError) as error:
        raise FleetError(str(error)) from error
    print(json.dumps(asdict(run), default=str))


def command_run_link(arguments: argparse.Namespace) -> None:
    try:
        run = open_execution().link(arguments.host, arguments.job, arguments.work_item, actor=arguments.actor)
    except (ValueError, LookupError) as error:
        raise FleetError(str(error)) from error
    print(json.dumps(asdict(run), default=str))


def command_library_link(arguments: argparse.Namespace) -> None:
    if arguments.project is not None:
        arguments.project = open_workspace().resolve_project(arguments.project)
    try:
        entry = open_library().link(arguments.url, project=arguments.project, work_item=arguments.work_item,
                                    title=arguments.title, actor=arguments.actor)
    except (ValueError, LookupError) as error:
        raise FleetError(str(error)) from error
    print(json.dumps(asdict(entry)))


def command_add(arguments: argparse.Namespace) -> None:
    host, job_id = resolve(arguments.job)
    steps = read_steps(arguments)
    if arguments.context:
        push_context(host, job_id, arguments.context)
    fleetd_arguments = ["add", job_id, "--steps-file", "/dev/stdin"] + (["--retry"] if arguments.retry else [])
    job = transport.call(host, fleetd_arguments, stdin_text=json.dumps(steps))
    console.print(f"[bold]{host.name}:{job_id}[/] {job['status']} · now {len(job['steps'])} step(s)")


def command_push(arguments: argparse.Namespace) -> None:
    host, job_id = resolve(arguments.job)
    push_context(host, job_id, arguments.paths)
    console.print(f"pushed {len(arguments.paths)} path(s) to {host.name}:~/.fleet/jobs/{job_id}/context/")


def command_pull(arguments: argparse.Namespace) -> None:
    host, job_id = resolve(arguments.job)
    destination = Path(arguments.destination or f"./fleet-{job_id}").resolve()
    destination.mkdir(parents=True, exist_ok=True)
    source = f"~/.fleet/jobs/{job_id}/outbox/"
    transport.rsync([host.rsync_target(os.path.expanduser(source) if host.is_local else source)], str(destination), host)
    console.print(f"outbox of {host.name}:{job_id} → {destination}")


def command_show(arguments: argparse.Namespace) -> None:
    host, job_id = resolve(arguments.job)
    job = transport.call(host, ["show", job_id, "--events", str(arguments.events)])
    if arguments.json:
        print(json.dumps(job))
        return
    tree = Tree(job_label(host.name, job))
    add_steps(tree, job, brief=False)
    console.print(tree)
    console.print(f"[dim]cwd {job['cwd']} · {job['permission']} · session {job.get('session_id')}[/]")
    for event in job["events"]:
        stamp = time.strftime("%H:%M:%S", time.localtime(event["ts"]))
        kind = event.get("tool") or event["kind"]
        line = Text(f"{stamp} {event.get('step', '')} ", "dim")
        line.append(f"{TOOL_ICON.get(kind, kind)} {event.get('summary', '')}", "red" if event["kind"] == "error" else "")
        console.print(line, highlight=False)


def command_tail(arguments: argparse.Namespace) -> None:
    host, job_id = resolve(arguments.job)
    command = host.fleetd_command(["events", job_id, "--lines", str(arguments.lines)] + (["-f"] if arguments.follow else []))
    process = subprocess.Popen(command, stdout=subprocess.PIPE, text=True)
    assert process.stdout is not None
    try:
        for line in process.stdout:
            event = json.loads(line)
            stamp = time.strftime("%H:%M:%S", time.localtime(event["ts"]))
            kind = event.get("tool") or event["kind"]
            print(f"{stamp} [{event.get('step', '-')}] {TOOL_ICON.get(kind, kind)} {event.get('summary', '')}", flush=True)
    except KeyboardInterrupt:
        process.terminate()


def command_attach(arguments: argparse.Namespace) -> None:
    host, job_id = resolve(arguments.job)
    command = host.shell_command(f"tmux -L fleet attach -t fleet-{job_id}", interactive=True)
    os.execvp(command[0], command)


def wait_for(references: list[str], *, step: int | None, timeout: float | None, as_json: bool,
             any_job: bool = False) -> None:
    """Block until the jobs finish. Exit code 0 if all finished jobs are done, 1 otherwise."""
    pending = {reference: resolve(reference) for reference in references}
    finished: dict[str, dict[str, Any]] = {}
    processes = {}
    for reference, (host, job_id) in pending.items():
        fleetd_arguments = ["wait", job_id] + (["--step", str(step)] if step is not None else [])
        fleetd_arguments += ["--timeout", str(timeout)] if timeout else []
        processes[reference] = subprocess.Popen(host.fleetd_command(fleetd_arguments), stdout=subprocess.PIPE, text=True)
    while processes:
        for reference, process in list(processes.items()):
            if process.poll() is None:
                continue
            output = (process.stdout.read() if process.stdout else "").strip().splitlines()
            finished[reference] = json.loads(output[-1]) if output else {"error": "no output"}
            del processes[reference]
            report_finished(reference, finished[reference], as_json=as_json)
        if any_job and finished:
            for process in processes.values():
                process.terminate()
            break
        time.sleep(0.5)
    sys.exit(0 if all(job.get("status") == "done" for job in finished.values()) else 1)


def report_finished(reference: str, job: dict[str, Any], *, as_json: bool) -> None:
    if as_json:
        print(json.dumps({"job": reference, **job}), flush=True)
        return
    if "error" in job:
        console.print(f"[red]{reference}: {job['error']}[/]")
        return
    icon, style = STATUS_STYLE.get(job["status"], ("?", ""))
    console.print(f"[{style}]{icon} {reference} {job['status']}[/] · {job['description']}")
    for result in job.get("results", []):
        step_icon, step_style = STEP_STYLE.get(result["status"], ("?", ""))
        console.print(f"  [{step_style}]{step_icon} {result['index'] + 1}. {result['title']}[/]")
        if result.get("result"):
            console.print(f"     {result['result']}", style="dim", markup=False, highlight=False)


def command_wait(arguments: argparse.Namespace) -> None:
    wait_for(arguments.jobs, step=arguments.step, timeout=arguments.timeout, as_json=arguments.json,
             any_job=arguments.any)


def command_result(arguments: argparse.Namespace) -> None:
    host, job_id = resolve(arguments.job)
    fleetd_arguments = ["result", job_id] + (["--step", str(arguments.step - 1)] if arguments.step else [])
    document = transport.call(host, fleetd_arguments)
    if arguments.json:
        print(json.dumps(document))
        return
    for result in document["results"]:
        print(f"## Step {result['index'] + 1} ({result['status']})\n\n{result['text'] or '(no result yet)'}\n")
    if document["outbox"]:
        print("Outbox (fetch with `fleet pull`):\n" + "\n".join(f"- {path}" for path in document["outbox"]))


def command_cancel(arguments: argparse.Namespace) -> None:
    host, job_id = resolve(arguments.job)
    job = transport.call(host, ["cancel", job_id] + (["--all-steps"] if arguments.all_steps else []))
    console.print(f"{host.name}:{job_id} {job['status']}")


def command_move(arguments: argparse.Namespace) -> None:
    for reference in arguments.jobs:
        host, job_id = resolve(reference)
        transport.call(host, ["mv", job_id, arguments.project])
        console.print(f"{host.name}:{job_id} → {arguments.project}")


def command_remove(arguments: argparse.Namespace) -> None:
    host, job_id = resolve(arguments.job)
    transport.call(host, ["rm", job_id])
    console.print(f"removed {host.name}:{job_id}")


def command_notify(arguments: argparse.Namespace) -> None:
    """Print one line whenever any step or job changes status — made for a Monitor/background watcher."""
    hosts = selected_hosts(arguments)
    known: dict[str, str] = {}
    first_pass = True
    while True:
        for report in transport.gather(hosts, ["ls", "--since-hours", "48"]):
            for job in report.jobs:
                reference = f"{report.host.name}:{job['id']}"
                for step in job["steps"]:
                    key = f"{reference}#{step['index']}"
                    if known.get(key) != step["status"]:
                        if not first_pass and step["status"] in ("done", "failed", "cancelled", "running"):
                            print(f"STEP {step['status'].upper()} {reference} step {step['index'] + 1}/{len(job['steps'])}: "
                                  f"{step['title']}" + (f" — {step['result'][:200]}" if step.get("result") else ""), flush=True)
                        known[key] = step["status"]
                if known.get(reference) != job["status"]:
                    if not first_pass and job["status"] in ("done", "failed", "cancelled", "stalled"):
                        print(f"JOB {job['status'].upper()} {reference} ({job['project']}): {job['description']}", flush=True)
                    known[reference] = job["status"]
        first_pass = False
        time.sleep(arguments.interval)


def command_host_add(arguments: argparse.Namespace) -> None:
    open_workspace()
    config = transport.load_config()
    config.setdefault("hosts", {})[arguments.name] = {"ssh": None if arguments.local else (arguments.ssh or arguments.name),
                                                      "python": arguments.python}
    transport.save_config(config)
    console.print(f"added {arguments.name}; now run: fleet install {arguments.name}")


def command_host_remove(arguments: argparse.Namespace) -> None:
    open_workspace()
    config = transport.load_config()
    config.get("hosts", {}).pop(arguments.name, None)
    transport.save_config(config)


def command_hosts(arguments: argparse.Namespace) -> None:
    for report in transport.gather(transport.configured_hosts(), ["ls"]):
        target = report.host.ssh_target or "(local)"
        state = f"[red]{report.error}[/]" if report.error else f"[green]ok[/] · {len(report.jobs)} active job(s)"
        console.print(f"[bold]{report.host.name}[/] {target} · {state}")


def command_library_add(arguments: argparse.Namespace) -> None:
    root = Path(arguments.path).expanduser().resolve()
    if not root.is_dir():
        raise FleetError(f"not a directory: {root}")
    open_workspace()
    config = transport.load_config()
    config.setdefault("libraries", {})[arguments.project] = str(root)
    transport.save_config(config)
    console.print(f"added library {arguments.project}: {root}")


def command_library_remove(arguments: argparse.Namespace) -> None:
    open_workspace()
    config = transport.load_config()
    config.get("libraries", {}).pop(arguments.project, None)
    transport.save_config(config)


def command_libraries(arguments: argparse.Namespace) -> None:
    for project, path in sorted(transport.load_config().get("libraries", {}).items()):
        console.print(f"[bold]{project}[/] {path}")


def parse_link(text: str) -> tuple[str, str]:
    """`host:label` → (host, label) for a configured host."""
    host, _, label = text.partition(":")
    if not host or not label:
        raise FleetError(f"expected host:label, got '{text}'")
    return transport.host_by_name(host).name, label


def command_project_add(arguments: argparse.Namespace) -> None:
    links = [parse_link(text) for text in arguments.link or []]

    def create(registry: projects.Registry) -> projects.Project:
        project = registry.create(arguments.name, arguments.repo or [])
        for host, label in links:
            registry.link(project.id, host, label)
        return project
    project = open_workspace().edit_registry(create)
    console.print(f"added project [bold]{project.id}[/] {escape(project.name)}")


def command_project_rename(arguments: argparse.Namespace) -> None:
    workspace = open_workspace()
    workspace.edit_registry(lambda registry: registry.rename(arguments.id, arguments.name))
    console.print(f"{arguments.id} → {escape(workspace.registry().get(arguments.id).name)}")


def command_project_link(arguments: argparse.Namespace) -> None:
    host, label = parse_link(arguments.link)
    link = open_workspace().edit_registry(lambda registry: registry.link(arguments.id, host, label))
    console.print(f"linked {escape(link.host)}:{escape(link.label)} → {arguments.id}")


def command_project_unlink(arguments: argparse.Namespace) -> None:
    host, _, label = arguments.link.partition(":")
    project_id = open_workspace().edit_registry(lambda registry: registry.unlink(host, label))
    console.print(f"unlinked {escape(host)}:{escape(label)} from {project_id}")


def command_project_merge(arguments: argparse.Namespace) -> None:
    """Merge project identity and free the other's floor in one transaction."""
    workspace = open_workspace()
    registry = workspace.registry()
    other = registry.get(arguments.other)
    workspace.merge(arguments.keep, arguments.other)
    keep = workspace.registry().get(arguments.keep)
    console.print(f"merged {arguments.other} {escape(other.name)} into [bold]{keep.id}[/] {escape(keep.name)}")
    for link in sorted(keep.links):
        console.print(f"  {escape(link.host)}:{escape(link.label)}")


def command_project_management(arguments: argparse.Namespace) -> None:
    try:
        open_records().register(arguments.id, Path(arguments.path), actor='user')
    except (ValueError, OSError) as error:
        raise FleetError(str(error)) from error


def command_project_repo_add(arguments: argparse.Namespace) -> None:
    open_workspace().edit_registry(lambda registry: registry.add_repository(arguments.id, arguments.url))


def command_project_repo_remove(arguments: argparse.Namespace) -> None:
    open_workspace().edit_registry(lambda registry: registry.remove_repository(arguments.id, arguments.url))


def observed_labels(registry: projects.Registry, hosts: list[Host]) -> tuple[list[tuple[str, str, str]], list[str]]:
    """(host, label, remote) for unlinked labels in jobs and sessions, plus per-host errors."""
    with ThreadPoolExecutor(max_workers=2) as pool:
        jobs = pool.submit(transport.gather, hosts, ["ls", "--all"])
        sessions = pool.submit(transport.gather_sessions, hosts)
        reports, by_host = jobs.result(), sessions.result()
    errors = [report.error for report in reports if report.error]
    directories: dict[str, dict[str, set[str]]] = defaultdict(lambda: defaultdict(set))
    for report in reports:
        for item in report.jobs + by_host.get(report.host.name, []):
            label, directory = item.get("project"), item.get("cwd")
            if label and directory and registry.project_for(report.host.name, label) is None:
                directories[report.host.name][directory].add(label)

    def remotes(host: Host) -> list[tuple[str, str, str]]:
        found = transport.repository_remotes(host, sorted(directories[host.name]))
        return [(host.name, label, url) for directory, urls in found.items()
                for label in sorted(directories[host.name][directory]) for url in urls]

    observed = []
    for host in hosts:
        if directories.get(host.name):
            try:
                observed += remotes(host)
            except FleetError as error:
                errors.append(str(error))
    return observed, errors


def command_project_list(arguments: argparse.Namespace) -> None:
    registry = open_workspace().registry()
    if not registry.projects:
        console.print("no registered projects — add one with: fleet project add <name> --link host:label")
    for project in sorted(registry.projects.values(), key=lambda project: (project.name.lower(), project.id)):
        console.print(f"[bold]{project.id}[/] {escape(project.name)}")
        for link in sorted(project.links):
            console.print(f"  {escape(link.host)}:{escape(link.label)}")
        for repository in project.repositories:
            console.print(f"  [dim]repo[/] {escape(repository)}")
    if not arguments.suggest or not any(project.repositories for project in registry.projects.values()):
        return
    observed, errors = observed_labels(registry, transport.configured_hosts())
    suggestions = list(dict.fromkeys((suggestion.link, suggestion.project_id)
                                     for suggestion in registry.suggest_links(observed)))
    if suggestions:
        console.print("\n[bold]suggested links[/] (repository matches)")
    for link, project_id in suggestions:
        target = f"{link.host}:{link.label}"
        console.print(f"  {escape(target)} → {project_id} {escape(registry.get(project_id).name)}"
                      f"  [dim]fleet project link {project_id} {escape(shlex.quote(target))}[/]")
    for error in errors:
        console.print(f"[yellow]no suggestions from {escape(error)}[/]")


def command_building_capacity(arguments: argparse.Namespace) -> None:
    """The building's floors: a deliberate setting, never raised as a side effect of starting work (ADR 0005)."""
    workspace = open_workspace()
    if arguments.floors is None:
        console.print(f"{workspace.capacity()} floors")
        return
    workspace.set_capacity(arguments.floors)
    console.print(f"the building has {arguments.floors} floors")


# Agents are often only on PATH in interactive login shells (nvm, pyenv), so ask those first.
# Each shell may set up a different PATH (e.g. nvm only in .bashrc), so every one is asked.
DETECT_SCRIPT = r"""
probe='echo "PATH=$PATH"; echo "claude=$(command -v claude)"; echo "codex=$(command -v codex)"'
for shell in zsh bash; do
  command -v $shell >/dev/null || continue
  $shell -lic "$probe" 2>/dev/null </dev/null | grep -E '^(PATH|claude|codex)='
done
eval "$probe"
"""


def merge_detected(output: str) -> dict[str, str | None]:
    """First hit for each agent wins; its directory goes on PATH so `#!/usr/bin/env node` resolves."""
    found: dict[str, str] = {}
    first_path = ""
    for line in output.splitlines():
        name, _, value = line.partition("=")
        if name == "PATH":
            first_path = first_path or value
        elif value.startswith("/") and name not in found:
            found[name] = value
    directories = list(dict.fromkeys(os.path.dirname(binary) for binary in found.values()))
    path = ":".join(directories + [entry for entry in first_path.split(":") if entry and entry not in directories])
    return {"path": path, "claude": found.get("claude"), "codex": found.get("codex")}


# Spelled out rather than $XDG_RUNTIME_DIR, which some sshd/PAM setups leave unset.
AGENT_SOCKET = "/run/user/$(id -u)/fleet-ssh-agent.sock"


def command_install(arguments: argparse.Namespace) -> None:
    """Copy fleetd to the host and record where its agent binaries live."""
    host = transport.host_by_name(arguments.name)
    destination = transport.REMOTE_FLEETD_PATH
    subprocess.run(host.shell_command("mkdir -p ~/.local/share/fleet"), check=True, capture_output=True)
    transport.rsync([str(transport.LOCAL_FLEETD_SOURCE)],
                    host.rsync_target(os.path.expanduser(destination) if host.is_local else destination), host)
    detected = subprocess.run(host.shell_command(DETECT_SCRIPT), capture_output=True, text=True, timeout=60).stdout
    agent_socket = subprocess.run(host.shell_command(f"test -S {AGENT_SOCKET} && echo {AGENT_SOCKET}"),
                                  capture_output=True, text=True, timeout=20).stdout.strip()
    if host.is_local and not agent_socket:
        agent_socket = os.environ.get("SSH_AUTH_SOCK", "")  # this machine's own agent already holds the keys
    settings = {**merge_detected(detected), "ssh_auth_sock": agent_socket or None}
    report = transport.call(host, ["configure", json.dumps(settings)])
    console.print(f"[bold]{host.name}[/] ({report['host']}) installed")
    for name in ("claude", "codex"):
        console.print(f"  {name}: {report['config'].get(name) or '[red]not found[/]'}")
    console.print(f"  ssh-agent for jobs: {agent_socket or '[yellow]none — jobs get no SSH_AUTH_SOCK[/]'}")
    if not report["tmux"]:
        console.print("  [red]tmux not found — jobs cannot start[/]")


def command_unlock(arguments: argparse.Namespace) -> None:
    """Add the host's key to its fleet ssh-agent; prompts for the passphrase once per boot."""
    if not sys.stdin.isatty():
        raise FleetError("unlock needs a real terminal to read the passphrase — run it in your own shell, "
                         "not via Claude Code's ! prefix")
    host = transport.host_by_name(arguments.name)
    key = f" {shlex.quote(arguments.key)}" if arguments.key else ""
    command = host.shell_command(f"SSH_AUTH_SOCK={AGENT_SOCKET} ssh-add{key} && SSH_AUTH_SOCK={AGENT_SOCKET} ssh-add -l",
                                 interactive=True)
    sys.exit(subprocess.run(command).returncode)


def command_web(arguments: argparse.Namespace) -> None:
    if arguments.fixture:
        serve_fixture(arguments.fixture, port=arguments.port, bind=arguments.bind, open_browser=arguments.open)
        return
    config = transport.load_config()
    serve(selected_hosts(arguments), port=arguments.port, bind=arguments.bind, open_browser=arguments.open,
          libraries=config.get("libraries", {}), project_labels=config.get("project_labels", {}),
          pipelines=config.get("pipelines", {}))


# --------------------------------------------------------------- parser


def command_status(arguments: argparse.Namespace) -> None:
    store = open_store()
    project = open_workspace(store).resolve_project(arguments.project)
    projection = project_status(project, open_work(store), open_attention(store),
                                open_execution(store), open_library(store), open_decisions(store))
    if arguments.json:
        print(json.dumps(projection))
        return
    print(f"Project: {projection['project']}")
    if not projection["work_items"]:
        print("No work items recorded.")
    for item in projection["work_items"]:
        print_status_item(item)
    for entry in projection["attention"]:
        print(f"  Attention ({entry['kind']}): {entry['headline']}")


def print_status_item(item: dict[str, Any], depth: int = 0) -> None:
    indent = "  " * depth
    print(f"{indent}{item['kind']}: {item['title']}")
    print(f"{indent}  Goal: {item['goal']}")
    progress = item["progress"]
    mark = ("unknown" if progress["basis"] == "unknown" else
            f"{progress['complete']}/{progress['total']} {progress['basis']}")
    print(f"{indent}  Progress: {mark}; condition: {item['condition']}")
    if item['kind'] == 'milestone':
        print(f"{indent}  Interruptions: {item['interruptions']}")
    next_step = "not recorded" if item["next_step"] is None else item["next_step"]
    print(f"{indent}  Next step: {next_step}")
    if item["no_follow_up_yet"] is True:
        print(f"{indent}  No follow-up yet")
    elif item["no_follow_up_yet"] is None:
        print(f"{indent}  Follow-up timing: unknown")
    print(f"{indent}  Runs:")
    for run in item["runs"]:
        print(f"{indent}    {run['id']} on {run['host']} ({run['remote_job_id']}): {run['status']}")
        for field in ("runtime", "reason", "start", "end", "last_observed", "usage"):
            value = "unknown" if run[field] is None else run[field]
            print(f"{indent}      {field.replace('_', ' ').capitalize()}: {value}")
    print(f"{indent}  Library:")
    for entry in item["library"]:
        title = "unknown" if entry["title"] is None else entry["title"]
        print(f"{indent}    {entry['kind']}: {title} ({entry['availability']}) — {entry['canonical_location']}")
    if item["resume_condition"] is not None:
        print(f"{indent}  Resume condition: {item['resume_condition']}")
    for criterion in item["criteria"]:
        print(f"{indent}  Criterion ({criterion['verification']}, {criterion['state']}): {criterion['text']}")
    for entry in item["attention"]:
        print(f"{indent}  Attention ({entry['kind']}): {entry['headline']}")
    for decision in item["decisions"]:
        print(f"{indent}  Decision {decision['id']}: {decision['question']}")
        print(f"{indent}    {decision['answer']} — {decision['actor']} at {decision['time']}")
    summary = item["summary"]
    if summary is not None:
        print(f"{indent}  Summary ({summary['authoring_role']}, {summary['updated']}):")
        for field in ("purpose", "done", "doing", "next"):
            print(f"{indent}    {field.capitalize()}: {summary[field]}")
    for child in item["children"]:
        print_status_item(child, depth + 1)


def command_work(arguments: argparse.Namespace) -> None:
    work = open_work()
    fields = vars(arguments).copy()
    command = fields.pop("work_operation")
    for name in ("handler", "command"):
        fields.pop(name, None)
    identity = fields.pop("id", None)
    try:
        if command == "criterion_add":
            reference = fields.pop("evidence_reference")
            result = fields.pop("required_result")
            if result is not None and reference is None:
                raise ValueError("required result needs an evidence reference")
            fields["specification"] = EvidenceSpecification(reference, result) if reference is not None else None
            item = work.add_criterion(identity, **fields)
        elif command == "meet":
            fields["evidence"] = tuple(fields["evidence"])
            item = work.meet(identity, **fields)
        elif command == "relate":
            item = work.relate(identity, fields.pop("to_item"), **fields)
        elif command == "add":
            fields['project'] = open_workspace().resolve_project(fields['project'])
            item = work.add(**fields)
        else:
            item = getattr(work, command)(identity, **fields)
        console.print_json(json.dumps(asdict(item), default=str))
    except (ValueError, LookupError, OSError) as error:
        raise FleetError(str(error)) from error


def add_work_parsers(commands) -> None:
    work = commands.add_parser("work", help="persistent work items").add_subparsers(required=True)
    for name in ("add", "set", "move", "relate", "ready"):
        action = work.add_parser(name)
        action.set_defaults(handler=command_work, work_operation=name)
        action.add_argument("--actor", required=True)
        action.add_argument("title" if name == "add" else "id")
        if name == "add":
            action.add_argument("--project", required=True)
            action.add_argument("--goal", required=True)
            action.add_argument("--kind", default="task")
            for field in ("parent", "focus", "next-step"):
                action.add_argument(f"--{field}")
        elif name == "set":
            for field in ("title", "goal", "kind", "condition", "resume-condition", "next-step", "focus"):
                action.add_argument(f"--{field}", default=argparse.SUPPRESS)
        elif name == "move":
            parent = action.add_mutually_exclusive_group(required=True)
            parent.add_argument("--parent")
            parent.add_argument("--root", dest="parent", action="store_const", const=None)
        elif name == "relate":
            action.add_argument("to_item")
            action.add_argument("--type", default="depends-on")
    criterion = commands.add_parser("criterion", help="work completion criteria").add_subparsers(required=True)
    add = criterion.add_parser("add")
    add.set_defaults(handler=command_work, work_operation="criterion_add")
    add.add_argument("id", help="work item ID")
    add.add_argument("text")
    add.add_argument("--verification", required=True, choices=("checked", "judged", "accepted"))
    add.add_argument("--evidence-reference", help="absolute path to recorded local evidence")
    add.add_argument("--required-result", help="required result field in JSON evidence")
    add.add_argument("--actor", required=True)
    meet = criterion.add_parser("meet")
    meet.set_defaults(handler=command_work, work_operation="meet")
    meet.add_argument("id", help="criterion ID")
    meet.add_argument("--evidence", action="append", default=[])
    meet.add_argument("--actor", required=True)
    summary = commands.add_parser("summary", help="work summaries").add_subparsers(required=True)
    action = summary.add_parser("set")
    action.set_defaults(handler=command_work, work_operation="set_summary")
    action.add_argument("id", help="work item ID")
    for field in ("purpose", "done", "doing", "next", "authoring-role", "actor"):
        action.add_argument(f"--{field}", required=True)


def command_answer(arguments: argparse.Namespace) -> None:
    try:
        decision = open_decisions().answer(arguments.id, arguments.answer, actor="user",
                                           next_step=arguments.next_step)
        console.print_json(json.dumps(asdict(decision), default=lambda value: value.isoformat()))
    except (ValueError, LookupError) as error:
        raise FleetError(str(error)) from error


def command_attention(arguments: argparse.Namespace) -> None:
    if arguments.attention_command in ('add', 'list') and arguments.project is not None:
        arguments.project = open_workspace().resolve_project(arguments.project)
    try:
        attention = open_attention()
        command = arguments.attention_command
        if command == "add":
            item = attention.raise_item(
                project=arguments.project, kind=arguments.kind, owner=arguments.owner,
                source=arguments.source, source_reference=arguments.source_reference,
                headline=arguments.headline, context_reference=arguments.context_reference,
                work_item=arguments.work_item, run=arguments.run, actor=arguments.actor)
        elif command == "list":
            items = attention.list(project=arguments.project, state=arguments.state)
            console.print_json(json.dumps([asdict(item) for item in items], default=str))
            return
        elif command == "ack":
            item = attention.acknowledge(arguments.id, actor=arguments.actor)
        elif command == "snooze":
            item = attention.snooze(arguments.id, until=datetime.fromisoformat(arguments.until), actor=arguments.actor)
        else:
            item = attention.resolve(arguments.id, details=arguments.details, actor=arguments.actor)
        console.print_json(json.dumps(asdict(item), default=str))
    except (ValueError, LookupError) as error:
        raise FleetError(str(error)) from error


def add_listing_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--host", action="append", help="only these hosts (repeatable)")
    parser.add_argument("--project", "-p", help="only this project")
    parser.add_argument("--by", dest="group_by", choices=("project", "host"), default="project")
    parser.add_argument("--all", "-a", action="store_true", help="include old finished jobs")
    parser.add_argument("--since", type=float, default=24, help="hours of finished jobs to show (default 24)")
    parser.add_argument("--brief", "-b", action="store_true", help="hide step lists")
    parser.add_argument("--no-sessions", dest="sessions", action="store_false",
                        help="leave out live interactive Claude/Codex sessions")


def add_step_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--step", "-s", action="append", help="a task prompt; repeat for a task list")
    parser.add_argument("--steps-file", "-f", help="markdown list (one step per item) or JSON list")
    parser.add_argument("--context", "-c", action="append", help="file/dir to copy into the job's context dir")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="fleet", description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)

    listing = commands.add_parser("ls", help="list jobs across hosts, grouped by project")
    add_listing_options(listing)
    listing.add_argument("--json", action="store_true")
    listing.set_defaults(handler=command_list)

    watch = commands.add_parser("watch", help="live-updating ls")
    add_listing_options(watch)
    watch.add_argument("--interval", "-n", type=float, default=3)
    watch.set_defaults(handler=command_watch)

    send = commands.add_parser("send", help="start a job (a task list) on a host")
    send.add_argument("--host", "-H", required=True)
    send.add_argument("--project", "-p", required=True)
    send.add_argument("--work-item", help="link the created job to stored work")
    send.add_argument("--description", "-d", required=True, help="one line: what this job is working on")
    send.add_argument("--agent", "-a", choices=("claude", "codex"), default="claude")
    send.add_argument("--cwd", "-C", required=True, help="working directory on the host")
    send.add_argument("--permission", help="claude: acceptEdits|bypassPermissions|plan|default; "
                                           "codex: read-only|workspace-write|danger-full-access")
    send.add_argument("--model", "-m")
    send.add_argument("--allow", action="append",
                      help="claude permission rule to pre-approve, e.g. 'Bash(ss:*)' (repeatable)")
    send.add_argument("--add-dir", action="append", help="extra directory on the host the claude agent may use (repeatable)")
    send.add_argument("--env", action="append", help="NAME=value set in the agent's environment (repeatable)")
    send.add_argument("--id")
    send.add_argument("--keep-going", action="store_true", help="continue to next step after a failure")
    send.add_argument("--hold", action="store_true", help="create but don't start")
    send.add_argument("--wait", "-w", action="store_true", help="block until the job finishes")
    send.add_argument("--json", action="store_true")
    add_step_options(send)
    send.set_defaults(handler=command_send)

    dispatch = commands.add_parser("dispatch", help="claim and dispatch work to a host")
    dispatch.add_argument("work_item")
    dispatch.add_argument("instruction")
    dispatch.add_argument("--host", required=True)
    dispatch.add_argument("--runtime", dest="agent", choices=("claude", "codex"), required=True)
    dispatch.add_argument("--cwd", required=True)
    dispatch.add_argument("--id")
    dispatch.add_argument("--json", action="store_true")
    dispatch.set_defaults(handler=command_dispatch_work, permission=None, model=None, allow=None,
                          add_dir=None, env=None, keep_going=False, hold=False, wait=False,
                          context=None, steps_file=None)

    orchestrate = commands.add_parser('orchestrate', help='start a controller-local orchestrator')
    orchestrate.add_argument('work_item')
    orchestrate.add_argument('--mandate', required=True, help='recorded mandate path')
    orchestrate.add_argument('--host', required=True, help='configured local controller host')
    orchestrate.add_argument('--runtime', dest='agent', choices=('claude', 'codex'), required=True)
    orchestrate.add_argument('--cwd', required=True)
    orchestrate.add_argument('--permission', help='runtime permission, as for fleet send')
    orchestrate.set_defaults(handler=command_orchestrate)
    control = commands.add_parser('control', help='activation-bound controller command')
    control.add_argument('activation')
    control.add_argument('operation', choices=('state', 'progress', 'meet', 'attention', 'dispatch', 'decide', 'summary', 'propose'))
    control.add_argument('payload', help='JSON object of command fields')
    control.set_defaults(handler=command_control)

    run = commands.add_parser("run", help="stored execution runs").add_subparsers(dest="run_command", required=True)
    run_link = run.add_parser("link", help="link an existing host job without fetching it")
    run_link.add_argument("host")
    run_link.add_argument("job")
    run_link.add_argument("work_item")
    run_link.add_argument("--actor", default="user")
    run_link.set_defaults(handler=command_run_link)
    resolve_unknown = run.add_parser("resolve-unknown", help="explicitly close an unknown run to permit retry")
    resolve_unknown.add_argument("run")
    resolve_unknown.set_defaults(handler=command_resolve_unknown)
    retry = run.add_parser("retry", help="retry an action after its run has a known end")
    retry.add_argument("run")
    retry.set_defaults(handler=command_run_retry)

    add = commands.add_parser("add", help="append steps to a job (restarts it if idle)")
    add.add_argument("job")
    add.add_argument("--retry", action="store_true", help="also re-queue failed/cancelled steps")
    add_step_options(add)
    add.set_defaults(handler=command_add)

    push = commands.add_parser("push", help="copy files into a job's context dir")
    push.add_argument("job")
    push.add_argument("paths", nargs="+")
    push.set_defaults(handler=command_push)

    pull = commands.add_parser("pull", help="copy a job's outbox here")
    pull.add_argument("job")
    pull.add_argument("destination", nargs="?")
    pull.set_defaults(handler=command_pull)

    show = commands.add_parser("show", help="job details and recent activity")
    show.add_argument("job")
    show.add_argument("--events", type=int, default=25)
    show.add_argument("--json", action="store_true")
    show.set_defaults(handler=command_show)

    tail = commands.add_parser("tail", help="activity stream of a job")
    tail.add_argument("job")
    tail.add_argument("--follow", "-f", action="store_true")
    tail.add_argument("--lines", "-n", type=int, default=40)
    tail.set_defaults(handler=command_tail)

    attach = commands.add_parser("attach", help="attach to the job's tmux session")
    attach.add_argument("job")
    attach.set_defaults(handler=command_attach)

    wait = commands.add_parser("wait", help="block until jobs finish; exit 0 only if all are done")
    wait.add_argument("jobs", nargs="+")
    wait.add_argument("--step", type=int, help="wait for this step index (0-based) only")
    wait.add_argument("--any", action="store_true", help="return when the first job finishes")
    wait.add_argument("--timeout", type=float)
    wait.add_argument("--json", action="store_true")
    wait.set_defaults(handler=command_wait)

    result = commands.add_parser("result", help="final messages of each step + outbox listing")
    result.add_argument("job")
    result.add_argument("--step", type=int, help="1-based step number")
    result.add_argument("--json", action="store_true")
    result.set_defaults(handler=command_result)

    cancel = commands.add_parser("cancel", help="stop the running step")
    cancel.add_argument("job")
    cancel.add_argument("--all-steps", action="store_true", help="also cancel upcoming steps")
    cancel.set_defaults(handler=command_cancel)

    move = commands.add_parser("mv", help="move jobs to another project")
    move.add_argument("jobs", nargs="+")
    move.add_argument("project")
    move.set_defaults(handler=command_move)

    remove = commands.add_parser("rm", help="delete a finished job's state")
    remove.add_argument("job")
    remove.set_defaults(handler=command_remove)

    notify = commands.add_parser("notify", help="stream one line per status change (for monitors)")
    notify.add_argument("--host", action="append")
    notify.add_argument("--interval", "-n", type=float, default=5)
    notify.set_defaults(handler=command_notify)

    hosts = commands.add_parser("hosts", help="configured hosts and reachability")
    hosts.set_defaults(handler=command_hosts)

    host = commands.add_parser("host", help="manage hosts").add_subparsers(dest="host_command", required=True)
    host_add = host.add_parser("add")
    host_add.add_argument("name")
    host_add.add_argument("--ssh", help="ssh target (default: the name)")
    host_add.add_argument("--local", action="store_true", help="this machine, no ssh")
    host_add.add_argument("--python", default="python3")
    host_add.set_defaults(handler=command_host_add)
    host_remove = host.add_parser("rm")
    host_remove.add_argument("name")
    host_remove.set_defaults(handler=command_host_remove)

    libraries = commands.add_parser("libraries", help="configured local project libraries")
    libraries.set_defaults(handler=command_libraries)
    library = commands.add_parser("library", help="manage local project libraries").add_subparsers(
        dest="library_command", required=True)
    library_link = library.add_parser("link", help="index an external URL; grants no access")
    library_link.add_argument("url")
    library_link.add_argument("--project", help="required when no work item is supplied")
    library_link.add_argument("--work-item")
    library_link.add_argument("--title", help="optional display label; omitted titles remain unknown")
    library_link.add_argument("--actor", default="user")
    library_link.set_defaults(handler=command_library_link)
    library_add = library.add_parser("add")
    library_add.add_argument("project")
    library_add.add_argument("path")
    library_add.set_defaults(handler=command_library_add)
    library_remove = library.add_parser("rm")
    library_remove.add_argument("project")
    library_remove.set_defaults(handler=command_library_remove)

    project = commands.add_parser("project", help="registered projects: stable IDs linked to host:label").add_subparsers(
        dest="project_command", required=True)
    project_add = project.add_parser("add", help="register a project with a new stable ID")
    project_add.add_argument("name")
    project_add.add_argument("--link", action="append", metavar="HOST:LABEL", help="link a host's label (repeatable)")
    project_add.add_argument("--repo", action="append", metavar="URL", help="repository remote, used to suggest links")
    project_add.set_defaults(handler=command_project_add)
    project_list = project.add_parser("ls", help="projects, their links, and suggested links")
    project_list.add_argument("--no-suggest", dest="suggest", action="store_false",
                              help="do not ask hosts for labels to suggest")
    project_list.set_defaults(handler=command_project_list)
    project_rename = project.add_parser("rename", help="change a project's name (its ID stays)")
    project_rename.add_argument("id")
    project_rename.add_argument("name")
    project_rename.set_defaults(handler=command_project_rename)
    project_link = project.add_parser("link", help="attach a host's label to a project")
    project_link.add_argument("id")
    project_link.add_argument("link", metavar="HOST:LABEL")
    project_link.set_defaults(handler=command_project_link)
    project_unlink = project.add_parser("unlink", help="detach a host's label from its project")
    project_unlink.add_argument("link", metavar="HOST:LABEL")
    project_unlink.set_defaults(handler=command_project_unlink)
    project_merge = project.add_parser("merge", help="fold a project registered by mistake into the older one")
    project_merge.add_argument("keep", metavar="KEEP-ID", help="the older project: keeps its ID and name")
    project_merge.add_argument("other", metavar="OTHER-ID", help="gives up its links, repositories and floor")
    project_merge.set_defaults(handler=command_project_merge)
    project_repo = project.add_parser("repo", help="repository remotes used to suggest links").add_subparsers(
        dest="project_repo_command", required=True)
    project_repo_add = project_repo.add_parser("add")
    project_repo_add.add_argument("id")
    project_repo_add.add_argument("url")
    project_repo_add.set_defaults(handler=command_project_repo_add)
    project_repo_remove = project_repo.add_parser("rm")
    project_repo_remove.add_argument("id")
    project_repo_remove.add_argument("url")
    project_repo_remove.set_defaults(handler=command_project_repo_remove)

    management = project.add_parser('management', help='register the management Git repository and migrate summaries')
    management.add_argument('id')
    management.add_argument('path')
    management.set_defaults(handler=command_project_management)

    add_work_parsers(commands)

    status = commands.add_parser("status", help="persisted project work and open attention")
    status.add_argument("project")
    status.add_argument("--json", action="store_true", help="emit the project projection")
    status.set_defaults(handler=command_status)

    answer = commands.add_parser("answer", help="record an answer; options use 1-based numbers")
    answer.add_argument("id")
    answer.add_argument("answer")
    answer.add_argument("--next-step")
    answer.set_defaults(handler=command_answer)

    attention = commands.add_parser("attention", help="stored questions, blockers and alerts").add_subparsers(
        dest="attention_command", required=True)
    attention_add = attention.add_parser("add")
    attention_add.add_argument("headline")
    for field in ("project", "kind", "owner", "source", "source-reference", "context-reference", "actor"):
        attention_add.add_argument(f"--{field}", required=True)
    attention_add.add_argument("--work-item")
    attention_add.add_argument("--run")
    attention_add.set_defaults(handler=command_attention)
    attention_list = attention.add_parser("list")
    attention_list.add_argument("--project")
    attention_list.add_argument("--state", choices=("open", "acknowledged", "snoozed", "resolved"))
    attention_list.set_defaults(handler=command_attention)
    for name in ("ack", "snooze", "resolve"):
        action = attention.add_parser(name)
        action.add_argument("id")
        action.add_argument("--actor", required=True)
        if name == "snooze":
            action.add_argument("--until", required=True, help="timezone-aware ISO timestamp")
        elif name == "resolve":
            action.add_argument("--details", required=True)
        action.set_defaults(handler=command_attention)

    building_parser = commands.add_parser("building", help="the deck's building: how many floors").add_subparsers(
        dest="building_command", required=True)
    building_capacity = building_parser.add_parser(
        "capacity", help=f"show or set how many projects can be live at once (1 to {projects.MAX_CAPACITY})")
    building_capacity.add_argument("floors", nargs="?", type=int)
    building_capacity.set_defaults(handler=command_building_capacity)

    install = commands.add_parser("install", help="install/upgrade fleetd on a host")
    install.add_argument("name")
    install.set_defaults(handler=command_install)

    unlock = commands.add_parser("unlock", help="add a key to the host's fleet ssh-agent (passphrase once per boot)")
    unlock.add_argument("name")
    unlock.add_argument("--key", help="key path on the host (default: ssh-add's defaults)")
    unlock.set_defaults(handler=command_unlock)

    web = commands.add_parser("web", help="serve the kitchen dashboard")
    web.add_argument("--host", action="append")
    web.add_argument("--port", type=int, default=8787)
    web.add_argument("--bind", default="127.0.0.1")
    web.add_argument("--open", action="store_true", help="open a browser tab")
    web.add_argument("--fixture", help=argparse.SUPPRESS)  # serve a recorded fleet from JSON, for tests
    web.set_defaults(handler=command_web)
    return parser


def main(argv: list[str] | None = None) -> None:
    arguments = build_parser().parse_args(argv)
    try:
        open_store()
        arguments.handler(arguments)
    except FleetError as error:
        error_console.print(f"[red]fleet: {error}[/]")
        sys.exit(2)
    except KeyboardInterrupt:
        sys.exit(130)


if __name__ == "__main__":
    main()
