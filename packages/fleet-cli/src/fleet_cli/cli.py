"""fleet — send tasks to Claude Code / Codex agents on other machines and watch them work."""
from __future__ import annotations

import argparse
import json
import re
import shlex
import sys
import time
from dataclasses import asdict, fields
from datetime import datetime
from pathlib import Path
from typing import Any

from rich.console import Console, Group
from rich.live import Live
from rich.markup import escape
from rich.text import Text
from rich.tree import Tree

from fleet.container import Container
from fleet.modules import workspace as projects
from fleet.modules.attention import ItemResolved
from fleet.projections.history import parse_moment, parse_since
from fleet.projections.project import filter_status
from fleet.modules import attention as attention_module
from fleet.modules.work import CONDITIONS, KINDS, RELATION_TYPES
from fleet.modules.execution import Run
from fleet.container import FleetError, Host, HostReport, TimeoutExpired
from fleet.container import DispatchRequest
from fleet.container import listing_arguments
from fleet.container import merge_detected as parse_detected
from fleet.container import validate_paths, default_actor as actor_identity
from fleet_cli.plugins import load_command

console = Console()
error_console = Console(stderr=True)

STATUS_STYLE = {
    "running": ("●", "bold green"), "queued": ("◌", "yellow"), "stalled": ("◍", "magenta"),
    "failed": ("✗", "bold red"), "blocked": ("⚑", "bold yellow"), "done": ("✓", "dim green"),
    "cancelled": ("⊘", "dim"),
}
STEP_STYLE = {
    "running": ("▶", "bold green"), "pending": ("○", "dim"), "done": ("✓", "green"),
    "failed": ("✗", "red"), "blocked": ("⚑", "yellow"), "cancelled": ("⊘", "dim"),
}
TODO_STYLE = {"in_progress": ("▸", "cyan"), "pending": ("·", "dim"), "completed": ("✓", "dim green")}
TOOL_ICON = {"bash": "$", "edit": "✎", "read": "📖", "search": "🔍", "web": "🌐", "think": "💭",
             "delegate": "👥", "plan": "📝", "other": "⚙"}
LIST_ITEM = re.compile(r"^\s*(?:[-*]\s+(?:\[[ xX]\]\s+)?|\d+[.)]\s+)(.+)$")


# ------------------------------------------------------------- job refs


def resolve(reference: str, *, container) -> tuple[Host, str]:
    return container.references().job(reference)


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
        if step.get("work_item") and not brief:
            line.append(f"  [{step['work_item']}]", "cyan")
        if step["status"] in ("done", "failed", "blocked") and step.get("result") and not brief:
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
    return listing_arguments(since=arguments.since, all_jobs=arguments.all)


def selected_hosts(arguments: argparse.Namespace, *, container) -> list[Host]:
    return container.jobs().selected_hosts(getattr(arguments, "host", None))


def gather_listing(hosts: list[Host], arguments: argparse.Namespace, *, container) -> tuple[list[HostReport], dict[str, list[dict[str, Any]]]]:
    return container.jobs().listing(hosts, since=arguments.since, all_jobs=arguments.all,
                               sessions=arguments.sessions, project=arguments.project)


# ------------------------------------------------------------- commands


def command_list(arguments: argparse.Namespace, *, container) -> None:
    reports, sessions = gather_listing(selected_hosts(arguments, container=container), arguments, container=container)
    if arguments.json:
        print(json.dumps([{"host": report.host.name, "error": report.error, "jobs": report.jobs,
                           "sessions": sessions.get(report.host.name, [])} for report in reports]))
        return
    console.print(render(reports, group_by=arguments.group_by, brief=arguments.brief, sessions=sessions))


def command_watch(arguments: argparse.Namespace, *, container) -> None:
    hosts = selected_hosts(arguments, container=container)
    with Live(console=console, screen=True, auto_refresh=False) as live:
        while True:
            reports, sessions = gather_listing(hosts, arguments, container=container)
            header = Text(f"fleet · {time.strftime('%H:%M:%S')} · every {arguments.interval}s · ctrl-c to quit\n", "dim")
            live.update(Group(header, render(reports, group_by=arguments.group_by, brief=arguments.brief,
                                             sessions=sessions)), refresh=True)
            time.sleep(arguments.interval)


def read_steps(arguments: argparse.Namespace) -> list[dict[str, Any]]:
    named = arguments.step_work_items or {}
    steps: list[dict[str, Any]] = [{"prompt": step, "work_item": named[index]} if index in named else {"prompt": step}
                                   for index, step in enumerate(arguments.step or [])]
    if arguments.steps_file:
        content = Path(arguments.steps_file).read_text()
        if arguments.steps_file.endswith(".json"):
            steps += [{"prompt": item} if isinstance(item, str) else item for item in json.loads(content)]
        else:
            items = [match.group(1).strip() for match in map(LIST_ITEM.match, content.splitlines()) if match]
            steps += [{"prompt": item} for item in items] or [{"prompt": content}]
    return steps


def push_context(host: Host, job_id: str, paths: list[str], *, container) -> None:
    container.context().push(host, job_id, paths)


def command_send(arguments: argparse.Namespace, *, container) -> None:
    command_dispatch(arguments, container=container)


def command_dispatch(arguments: argparse.Namespace, *, container) -> None:
    steps = read_steps(arguments)
    request = DispatchRequest(**{field.name: getattr(arguments, field.name) for field in fields(DispatchRequest)})
    result = container.dispatch().send(request, steps)
    intent, guidance, current, job = (result[name] for name in ('intent', 'guidance', 'current', 'job'))
    if not intent.created:
        reference = f"{intent.run.host}:{intent.run.remote_job_id}"
        if arguments.json:
            print(json.dumps({"job": reference, "status": current.status,
                              "run": intent.run.id, "action": intent.run.action, "steps": len(steps)}))
        else:
            console.print(f"{reference} already sent with this --id: run {intent.run.id[:8]} is {current.status}; "
                          f"nothing new started", markup=False)
        return
    reference = f"{intent.run.host}:{job['id']}"
    if arguments.json:
        print(json.dumps({"job": reference, "status": job["status"], "steps": len(job["steps"]),
                          "run": intent.run.id, "permission": job.get("permission"), "guidance": guidance}))
    else:
        console.print(f"[bold]{reference}[/] {job['status']} · {len(job['steps'])} step(s) · {job['description']}")
        sent = "no guidance" if guidance is None else "guidance " + describe_guidance(guidance)
        console.print(f"  run {intent.run.id[:8]} · {arguments.agent} · permission "
                      f"{job.get('permission') or 'not reported by the host'} · {sent}", markup=False)
    if arguments.wait:
        wait_for([reference], step=None, timeout=None, as_json=arguments.json, container=container)


def deliver_dispatch(run: Run, *, reconcile: bool = False, container) -> dict:
    return container.dispatch().deliver(run, reconcile=reconcile)


def push_guided_context(host: Host, job_id: str, paths: list[str], guidance: dict | None, *, container) -> None:
    container.context().push_guided(host, job_id, paths, guidance)


def command_triage_policy(arguments: argparse.Namespace, *, container) -> None:
    try:
        policy_service = container.triage_policy()
        project, before = policy_service.show(arguments.project)
        if arguments.policy_command == 'set':
            body = Path(arguments.file).read_text()
            project, before, current, unchanged = policy_service.set(arguments.project, body, actor=arguments.actor)
            policy = current['policy']
            previous = None if before is None else before['policy']
            if unchanged:
                print(f"Triage policy for {project}: unchanged, version {before['version']['number']}")
                return
            print(f"Recorded triage policy for {project}, version {current['version']['number']} "
                  f"by {current['version']['actor']} ({current['version']['revision'][:12]})")
            for field, value in policy.items():
                if previous is None or previous[field] != value:
                    old = 'not recorded' if previous is None else json.dumps(previous[field])
                    print(f"  {field}: {old} -> {json.dumps(value)}")
            print('Future triage activations use this policy; active runs keep their pinned version. '
                  'Existing attention ownership is unchanged. Triage cannot complete work or judge criteria.')
        elif getattr(arguments, 'json', False):
            print(json.dumps(before))
        elif before is None:
            print(f"No triage policy recorded for {project}; set one with: "
                  f"fleet triage policy set {project} --file F --actor A. Setup example: docs/triage-policy.md")
        else:
            print(f"Triage policy for {project}, version {before['version']['number']} "
                  f"by {before['version']['actor']} ({before['version']['revision'][:12]})")
            for field, value in before['policy'].items():
                label = field.replace('_', ' ').capitalize()
                print(f"  {label}: {json.dumps(value) if not isinstance(value, str) else value}")
            print('Setup example and field explanations: docs/triage-policy.md')
    except (ValueError, LookupError, OSError) as error:
        raise FleetError(str(error)) from error


def command_triage_status(arguments: argparse.Namespace, *, container) -> None:
    project, result = container.triage_policy().status(arguments.project)
    if getattr(arguments, 'json', False):
        print(json.dumps(result))
        return
    print(f"Triage for {project}")
    policy_error = result.get('policy_error')
    print(f"Mandate: {result['mandate_version'] or ('unreadable' if policy_error else 'No confirmed triage policy')}")
    if policy_error:
        print(f"Policy error: {policy_error}; delegation unavailable")
    print(f"Queue: {len(result['queue'])} agent-owned items")
    for identity in result['queue']:
        print(f"  {identity} — take back: fleet attention take {identity} --actor ACTOR")
    run = result['live_run']
    print(f"Live run: {run['id'] + ' (' + run['status'] + ')' if run else 'none'}")
    print(f"Budget: {result['budget_left']} runs left; resets at {result['budget_resets_at']}" if result['budget_left'] is not None else 'Budget: unavailable; policy unreadable' if policy_error else 'Budget: unavailable; no confirmed triage policy')
    wait = result['oldest_wait_seconds']
    print(f"Oldest queue wait: {int(wait)} seconds" if wait is not None else 'Oldest queue wait: not recorded' if result['queue'] else 'Oldest queue wait: no queued items')
    print(f"Delivery error: {result['delivery_error'] or 'none recorded'}")
    print(f"Pending publications: {len(result['pending_publications'])}")
    if result['mandate_version'] is None and result['queue']:
        print(f"{len(result['queue'])} agent-owned items cannot be serviced; take them back or {'restore access to the policy' if policy_error else 'record a policy'}.")
    print(f"Inspect policy: fleet triage policy show {project}; setup example: docs/triage-policy.md")
    if run:
        print(f"Inspect run: fleet run show {run['id']}; do not launch a duplicate while its outcome is unknown.")


def command_orchestrate(arguments: argparse.Namespace, *, container) -> None:
    activation, intent = container.dispatch().orchestrate(host=arguments.host, work_item=arguments.work_item,
        mandate=arguments.mandate, agent=arguments.agent, cwd=arguments.cwd, permission=arguments.permission)
    print(json.dumps(dict(activation=activation.id, run=intent.run.id, mandate_version=activation.mandate_version)))


def command_control(arguments: argparse.Namespace, *, container) -> None:
    try:
        payload = json.loads(arguments.payload)
    except (ValueError, TypeError) as error:
        raise FleetError(str(error)) from error
    result = container.dispatch().control(arguments.activation, arguments.operation, payload)
    print(json.dumps(result if isinstance(result, dict) else asdict(result), default=str))


def command_run_retry(arguments: argparse.Namespace, *, container) -> None:
    run = container.dispatch().retry(arguments.run, actor=arguments.actor)
    print(json.dumps(asdict(run), default=str))


def command_dispatch_work(arguments: argparse.Namespace, *, container) -> None:
    arguments.project = container.dispatch().project_for_work(arguments.work_item)
    arguments.description = arguments.instruction
    arguments.step = [arguments.instruction]
    command_dispatch(arguments, container=container)


def command_resolve_unknown(arguments: argparse.Namespace, *, container) -> None:
    try:
        run = container.execution().resolve_unknown(arguments.run, actor=arguments.actor)
    except (ValueError, LookupError) as error:
        raise FleetError(str(error)) from error
    print(json.dumps(asdict(run), default=str))


def command_run_link(arguments: argparse.Namespace, *, container) -> None:
    try:
        run = container.execution().link(arguments.host, arguments.job, arguments.work_item, actor=arguments.actor)
    except (ValueError, LookupError) as error:
        raise FleetError(str(error)) from error
    print(json.dumps(asdict(run), default=str))


def command_library_link(arguments: argparse.Namespace, *, container) -> None:
    if arguments.project is not None:
        arguments.project = container.initialized_workspace().resolve_project(arguments.project)
    try:
        entry = container.library().link(arguments.url, project=arguments.project, work_item=arguments.work_item,
                                    title=arguments.title, actor=arguments.actor)
    except (ValueError, LookupError) as error:
        raise FleetError(str(error)) from error
    print(json.dumps(asdict(entry)))


def command_add(arguments: argparse.Namespace, *, container) -> None:
    host, job_id = resolve(arguments.job, container=container)
    steps = read_steps(arguments)
    job, details = container.jobs().add(host, job_id, steps, context=arguments.context, retry=arguments.retry,
                                 no_answer=arguments.no_answer, actor=arguments.actor)
    if details is not None:
        console.print(f"[bold]{host.name}:{job_id}[/] {details} (--no-answer appends instead)")
    else:
        console.print(f"[bold]{host.name}:{job_id}[/] {job['status']} · now {len(job['steps'])} step(s)")


def waiting_step(host: Host, job_id: str, *, container) -> int | None:
    return container.jobs().waiting_step(host, job_id)


def answer_waiting_step(host: Host, job_id: str, step: int, steps: list[dict[str, Any]], actor: str, *, container) -> None:
    details = container.jobs().answer_waiting_step(host, job_id, step, steps, actor)
    console.print(f"[bold]{host.name}:{job_id}[/] {details} (--no-answer appends instead)")


def command_start(arguments: argparse.Namespace, *, container) -> None:
    host, job_id = resolve(arguments.job, container=container)
    job = container.jobs().start(host, job_id)
    console.print(f"started {host.name}:{job_id}: its agent works through the job's pending steps "
                  f"({job['status']})", markup=False)


def command_push(arguments: argparse.Namespace, *, container) -> None:
    host, job_id = resolve(arguments.job, container=container)
    push_context(host, job_id, arguments.paths, container=container)
    console.print(f"pushed {len(arguments.paths)} path(s) to {host.name}:~/.fleet/jobs/{job_id}/context/")


def command_pull(arguments: argparse.Namespace, *, container) -> None:
    host, job_id = resolve(arguments.job, container=container)
    destination = container.context().pull(host, job_id, arguments.destination)
    console.print(f"outbox of {host.name}:{job_id} → {destination}")


def command_show(arguments: argparse.Namespace, *, container) -> None:
    host, job_id = resolve(arguments.job, container=container)
    job = container.jobs().show(host, job_id, events=arguments.events)
    if arguments.json:
        print(json.dumps(job))
        return
    tree = Tree(job_label(host.name, job))
    add_steps(tree, job, brief=False)
    console.print(tree)
    console.print(f"[dim]cwd {job['cwd']} · {job['permission']} · session {job.get('session_id')}[/]")
    console.print(workspace_line(job), highlight=False)
    decisions = job.get("decisions_since_dispatch", [])
    console.print(f"Decisions since dispatch: {len(decisions)}", highlight=False)
    for decision in decisions:
        console.print(f"{decision['id']} · {decision['question']}\nAnswer: {decision['answer']}\nActor: {decision['actor']}\nPrinciple: {decision['principle']}", highlight=False, markup=False)
    for event in job["events"]:
        stamp = time.strftime("%H:%M:%S", time.localtime(event["ts"]))
        kind = event.get("tool") or event["kind"]
        line = Text(f"{stamp} {event.get('step', '')} ", "dim")
        line.append(f"{TOOL_ICON.get(kind, kind)} {event.get('summary', '')}", "red" if event["kind"] == "error" else "")
        console.print(line, highlight=False)

def workspace_line(job: dict[str, Any]) -> Text:
    """Where the job works: repository, worktree, branch or detached head, and uncommitted paths."""
    workspace = job.get("workspace")
    if workspace is None:
        # A worker older than workspace reports sends neither key.
        return Text(f"workspace unknown: {job.get('workspace_reason') or 'not reported by this worker'}", "dim")
    line = Text("repo ", "dim")
    line.append(workspace["repository"])
    if workspace["linked_worktree"]:
        line.append(" · worktree ", "dim")
        line.append(workspace["toplevel"])
    line.append(" · ", "dim")
    line.append("detached" if workspace["detached"] else workspace["branch"], "yellow" if workspace["detached"] else "cyan")
    line.append(f" @ {workspace['head'] or 'no commits'}", "dim")
    line.append(f" · {workspace['dirty']} uncommitted" if workspace["dirty"] else " · clean",
                "yellow" if workspace["dirty"] else "dim")
    return line


def command_tail(arguments: argparse.Namespace, *, container) -> None:
    host, job_id = resolve(arguments.job, container=container)
    for event in container.jobs().events(host, job_id, lines=arguments.lines, follow=arguments.follow):
        stamp = time.strftime("%H:%M:%S", time.localtime(event["ts"]))
        kind = event.get("tool") or event["kind"]
        print(f"{stamp} [{event.get('step', '-')}] {TOOL_ICON.get(kind, kind)} {event.get('summary', '')}", flush=True)


def command_attach(arguments: argparse.Namespace, *, container) -> None:
    host, job_id = resolve(arguments.job, container=container)
    container.jobs().attach(host, job_id)


def wait_for(references: list[str], *, step: int | None, timeout: float | None, as_json: bool,
             any_job: bool = False, container) -> None:
    pending = {reference: resolve(reference, container=container) for reference in references}
    finished = {}
    for reference, job in container.jobs().wait(pending, step=step, timeout=timeout, any_job=any_job):
        finished[reference] = job
        report_finished(reference, job, as_json=as_json)
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


def command_wait(arguments: argparse.Namespace, *, container) -> None:
    wait_for(arguments.jobs, step=arguments.step, timeout=arguments.timeout, as_json=arguments.json,
             any_job=arguments.any, container=container)


def command_result(arguments: argparse.Namespace, *, container) -> None:
    host, job_id = resolve(arguments.job, container=container)
    document = container.jobs().result(host, job_id, step=arguments.step)
    if arguments.json:
        print(json.dumps(document))
        return
    for result in document["results"]:
        print(f"## Step {result['index'] + 1} ({result['status']})\n\n{result['text'] or '(no result yet)'}\n")
    if document["outbox"]:
        print("Outbox (fetch with `fleet pull`):\n" + "\n".join(f"- {path}" for path in document["outbox"]))


def command_cancel(arguments: argparse.Namespace, *, container) -> None:
    host, job_id = resolve(arguments.job, container=container)
    job = container.jobs().cancel(host, job_id, all_steps=arguments.all_steps)
    console.print(f"{host.name}:{job_id} {job['status']}")

def resolve_job_or_session(reference: str, *, container) -> tuple[Host, str]:
    return container.references().job_or_session(reference)


def command_move(arguments: argparse.Namespace, *, container) -> None:
    targets = [resolve_job_or_session(reference, container=container) for reference in arguments.jobs]
    for host, job_id, identity, label in container.jobs().move(arguments.project, targets):
        console.print(f"{host.name}:{job_id} → {identity} ({label})")


def command_remove(arguments: argparse.Namespace, *, container) -> None:
    host, job_id = resolve(arguments.job, container=container)
    removed = container.jobs().remove(host, job_id, force=arguments.force)
    if "outbox_files" not in removed:   # fleetd before audit 1 reports only the id
        console.print(f"removed {host.name}:{job_id} for good (this host's fleetd does not count what it deleted)")
        return
    console.print(f"removed {removed['status']} job {host.name}:{job_id} for good: its directory, "
                  f"{removed['outbox_files']} outbox file(s) and {removed['results']} step result(s)")


def history_local_time(value: str | None) -> str:
    if value is None:
        return 'unknown'
    return datetime.fromisoformat(value.replace('Z', '+00:00')).astimezone().strftime('%Y-%m-%d %H:%M')


def history_duration(seconds: float | None) -> str:
    if seconds is None:
        return 'unknown'
    minutes = int(max(0, seconds) // 60)
    if minutes == 0:
        return '<1m' if seconds > 0 else '0m'
    hours, minutes = divmod(minutes, 60)
    return f'{hours}h{minutes:02d}m' if hours else f'{minutes}m'


def command_history_runs(arguments: argparse.Namespace, *, container) -> None:
    try:
        container.initialized_workspace()
        result = container.history_runs(
            **{key: getattr(arguments, key) for key in ("project", "work_item", "descendants", "host", "status",
                "kind", "unlinked", "since", "until", "limit")})
    except (ValueError, LookupError) as error:
        raise FleetError(str(error)) from error
    if arguments.json:
        print(json.dumps(result))
        return
    if result["empty_reason"]:
        print(result["empty_reason"])
    else:
        rows = [["STARTED (LOCAL)", "DURATION", "STATUS", "KIND", "HOST", "WORK", "BRANCH / CHANGE", "RUN"]]
        for run in result["runs"]:
            work = f"{run['work_title']} ({run['work_item'][:8]})" if run["work_item"] else f"unlinked · {run['label'] or 'label unknown'}"
            branch = (run["workspace"] or {}).get("branch")
            changes = f"{run['commit_count']} commits · {run['push_count']} pushes" if run['commit_count'] is not None else "git unknown"
            offline = f" · offline since {history_local_time(run['offline_since'])}" if run['offline_since'] else ""
            rows.append([history_local_time(run['start']), history_duration(run['duration_seconds']),
                         run.get('status_label', run['status']), run['kind'], run['host'], work,
                         f"{branch or 'branch unknown'} · {changes}{offline}", run['id'][:8]])
            if not branch and run['workspace_reason']:
                rows[-1][6] += f" ({run['workspace_reason']})"
        widths = [max(len(row[column]) for row in rows) for column in range(len(rows[0]))]
        for row in rows:
            print("  ".join(cell.ljust(width) for cell, width in zip(row, widths)).rstrip())
    print(f"{len(result['runs'])} of {result['total']} runs; --limit to see more")


def stored_run_detail(identity: str, documents=None, *, container) -> dict:
    return container.kept_run_detail(identity=identity, **({} if documents is None else {'documents': documents}))


def command_run_show(arguments: argparse.Namespace, *, container) -> None:
    try:
        detail = stored_run_detail(arguments.id, container=container)
    except (ValueError, LookupError) as error:
        raise FleetError(str(error)) from error
    if arguments.json:
        print(json.dumps(detail))
        return
    run = detail["run"]
    print(f"Run {run['id']}: {run.get('status_label', run['status'])} ({run['kind']})")
    if run['offline_since']:
        print(f"Host offline since {run['offline_since']}; worker state is last known.")
    for key in ("action_source", "work_item", "project", "host", "remote_job_id", "cwd", "start", "end", "reason", "workspace", "workspace_reason"):
        print(f"{key}: {run[key] if run[key] is not None else 'not recorded'}")
    for step in detail["steps"]:
        print(f"Step {step['index'] + 1}: {step['title']} ({step['status']})")
        print(json.dumps(step["git"], indent=2))
    print("Documents:")
    if not detail["documents"] and not detail["kept_documents"]:
        print("  No documents were recorded for this run.")
    for document in detail["documents"]:
        print(f"  {document['title']}: {document['canonical_location']} ({document['availability']})")
    for document in detail["kept_documents"]:
        print(f"  {document['name']}: {'kept' if document['stored'] else document['error'] or 'not copied yet'}")
    trace = detail["trace"]
    if trace.get("copy_error"):
        print(f"Trace copy error: {trace['copy_error']}")
    print(f"Trace: {trace['events']['availability']} "
          f"({trace['events'].get('bytes', 0)} bytes)" if trace['events']['availability'] == 'kept'
          else f"Trace: {trace['events']['reason']}")
    source = trace["source"]
    if source:
        removed = f" at {source['removed_at']}" if source.get("removed_at") else ""
        print(f"Worker events: {run['host']}:{source.get('path') or 'path not recorded'} ({source['availability']}{removed})")
        for raw in source.get("raw", []):
            print(f"Raw: {run['host']}:{raw['path']} ({raw['availability']}{removed})")


def command_store_usage(arguments: argparse.Namespace, *, container) -> None:
    result = container.storage_usage()
    if arguments.json:
        print(json.dumps(result))
        return
    print(f"SQLite: {result['bytes']} bytes at {result['path']}")
    for name, count in sorted(result["tables"].items()):
        print(f"  {name}: {count} rows")
    for name in ("documents", "traces"):
        print(f"{name}: {result[name]['files']} files, {result[name]['bytes']} bytes at {result[name]['path']}")


def command_notify(arguments: argparse.Namespace, *, container) -> None:
    hosts = selected_hosts(arguments, container=container)
    notifications = container.notifications()
    while True:
        for event in notifications.update(container.jobs().notification_reports(hosts)):
            if event['kind'] == 'host_down':
                print(f"HOST DOWN {event['host']} since {event['since']}: {event['error']}", flush=True)
            elif event['kind'] == 'host_up':
                print(f"HOST UP {event['host']}", flush=True)
            elif event['kind'] == 'step':
                step, reference = event['step'], event['reference']
                print(f"STEP {step['status'].upper()} {reference} step {step['index'] + 1}/{event['total']}: "
                      f"{step['title']}" + (f" — {step['result'][:200]}" if step.get('result') else ''), flush=True)
            else:
                job, reference = event['job'], event['reference']
                print(f"JOB {job['status'].upper()} {reference} ({job['project']}): {job['description']}", flush=True)
        time.sleep(arguments.interval)


def command_host_add(arguments: argparse.Namespace, *, container) -> None:
    entry, previous = container.configuration().host_add(arguments.name, local=arguments.local, ssh=arguments.ssh, python=arguments.python)
    if previous is None:
        console.print(f"added {arguments.name} ({describe_host(entry)}); now run: fleet install {arguments.name}",
                      markup=False)
    else:
        console.print(f"replaced {arguments.name} (was {describe_host(previous)}, now {describe_host(entry)}); "
                      f"now run: fleet install {arguments.name}", markup=False)


def describe_host(entry: dict) -> str:
    return "local" if entry.get("ssh") is None else f"ssh {entry['ssh']}"


def command_host_remove(arguments: argparse.Namespace, *, container) -> None:
    entry, links = container.configuration().host_remove(arguments.name)
    console.print(f"removed host {arguments.name} ({describe_host(entry)}) from the config; "
                  f"its jobs stay on the host", markup=False)
    if links:
        console.print(f"projects still link it: {', '.join(links)} (fleet project unlink HOST:LABEL)", markup=False)


def command_hosts(arguments: argparse.Namespace, *, container) -> None:
    reports = container.jobs().host_reports()
    if not reports:
        print("No hosts configured; add one with: fleet host add NAME --ssh TARGET (or --local)")
        return
    for report in reports:
        target = report.host.ssh_target or "(local)"
        state = f"[red]{report.error}[/]" if report.error else f"[green]ok[/] · {len(report.jobs)} active job(s)"
        console.print(f"[bold]{report.host.name}[/] {target} · {state}")


def local_library_key(project: str, libraries: dict[str, Any], *, container) -> str:
    return container.configuration().library_key(project, libraries)


def command_library_add(arguments: argparse.Namespace, *, container) -> None:
    arguments.project, root = container.configuration().library_add(arguments.project, arguments.path, recursive=arguments.recursive)
    console.print(f"added library {arguments.project}: {root}{' (every folder)' if arguments.recursive else ''}")


def command_library_remove(arguments: argparse.Namespace, *, container) -> None:
    arguments.project, path = container.configuration().library_remove(arguments.project)
    console.print(f"removed library {arguments.project} ({path}) from the config; its files stay", markup=False)


def command_libraries(arguments: argparse.Namespace, *, container) -> None:
    libraries = container.configuration().libraries()
    if not libraries:
        print("No libraries configured; add one with: fleet library add PROJECT PATH")
    for project, entry in sorted(libraries.items()):
        if isinstance(entry, str):
            console.print(f"[bold]{project}[/] {entry}")
        else:
            console.print(f"[bold]{project}[/] {entry['path']}{' (every folder)' if entry.get('recursive') else ''}")


def parse_link(text: str, *, container) -> tuple[str, str]:
    return container.projects().parse_link(text)


def command_project_add(arguments: argparse.Namespace, *, container) -> None:
    project = container.projects().add(arguments.name, arguments.repo or [], arguments.link or [])
    console.print(f"added project [bold]{project.id}[/] {escape(project.name)}")


def command_project_rename(arguments: argparse.Namespace, *, container) -> None:
    project = container.projects().rename(arguments.id, arguments.name)
    console.print(f"{project.id} → {escape(project.name)}")


def command_project_link(arguments: argparse.Namespace, *, container) -> None:
    arguments.id, link, count, documents = container.projects().link(arguments.id, arguments.link)
    console.print(f"linked {escape(link.host)}:{escape(link.label)} → {arguments.id}; "
                  f"{count} earlier runs assigned; {documents} retained jobs moved")


def command_project_unlink(arguments: argparse.Namespace, *, container) -> None:
    host, label, project_id = container.projects().unlink(arguments.link)
    console.print(f"unlinked {escape(host)}:{escape(label)} from {project_id}")


def command_project_merge(arguments: argparse.Namespace, *, container) -> None:
    other, keep, result = container.projects().merge(arguments.keep, arguments.other)
    arguments.other = other.id
    console.print(f"merged {arguments.other} {escape(other.name)} into [bold]{keep.id}[/] {escape(keep.name)}")
    console.print("  moved " + ", ".join(f"{count} {kind.replace('_', ' ')}" for kind, count in result.counts.items()))
    floor_feedback = f"floor {result.freed} is freed" if result.freed is not None else "it held no floor"
    console.print(f"  deleted project {arguments.other} for good; {floor_feedback}")
    for link in sorted(keep.links):
        console.print(f"  {escape(link.host)}:{escape(link.label)}")


def command_project_management(arguments: argparse.Namespace, *, container) -> None:
    arguments.id, path, moved = container.projects().management(arguments.id, arguments.path, actor=arguments.actor)
    console.print(f"registered {path} as the management repository of {arguments.id}; {moved} summaries moved "
                  f"out of the store into it. This is permanent.", markup=False)


def guidance_subject(reference: str, *, container) -> tuple[str, str | None]:
    return container.references().guidance(reference)


def describe_version(version) -> str:
    run = "" if version.source_run is None else f", run {version.source_run}"
    return f"version {version.number} by {version.actor} at {version.time}{run} ({version.revision[:12]})"


def command_guidance_show(arguments: argparse.Namespace, *, container) -> None:
    project, epic = guidance_subject(arguments.subject, container=container)
    try:
        guidance = container.records().guidance(project, epic, number=arguments.version)
    except (ValueError, LookupError) as error:
        raise FleetError(str(error)) from error
    name = f"charter of epic {epic}" if epic else f"constitution of {project}"
    if guidance is None:
        raise FleetError(f"no {name} recorded; write one with: fleet guidance edit {arguments.subject} --file F --actor A")
    if arguments.json:
        print(json.dumps(asdict(guidance)))
        return
    print(f"{name[0].upper()}{name[1:]}, {describe_version(guidance.version)}")
    if epic is not None:
        if guidance.constitution is None:
            print(f"Inherits no constitution: none recorded for {project}")
        else:
            inherited = ("none (written before it)" if guidance.inherits is None
                         else f"constitution version {guidance.inherits.number}")
            print(f"Inherits {inherited}; current constitution version {guidance.constitution.number}")
    print()
    print(guidance.body, end="" if guidance.body.endswith("\n") else "\n")


def command_guidance_edit(arguments: argparse.Namespace, *, container) -> None:
    project, epic = guidance_subject(arguments.subject, container=container)
    if arguments.file is None and sys.stdin.isatty():
        error_console.print("Reading Markdown from stdin; end with Ctrl-D (or pass --file).", markup=False)
    try:
        body = sys.stdin.read() if arguments.file is None else Path(arguments.file).read_text()
        guidance = container.records().write_guidance(project, body, epic=epic, actor=arguments.actor,
                                                 source_run=arguments.run)
    except (ValueError, LookupError, OSError) as error:
        raise FleetError(str(error)) from error
    print(f"recorded {guidance.path} {describe_version(guidance.version)}")


def command_guidance_promote(arguments: argparse.Namespace, *, container) -> None:
    try:
        guidance = container.promote_decision(epic=arguments.epic, decision=arguments.decision, actor=arguments.actor)
    except (ValueError, LookupError) as error:
        raise FleetError(str(error)) from error
    print(f"recorded {guidance.path} {describe_version(guidance.version)}")


def command_guidance_history(arguments: argparse.Namespace, *, container) -> None:
    project, epic = guidance_subject(arguments.subject, container=container)
    try:
        versions = container.records().guidance_history(project, epic)
    except (ValueError, LookupError) as error:
        raise FleetError(str(error)) from error
    if arguments.json:
        print(json.dumps([asdict(version) for version in versions]))
        return
    if not versions:
        print("No versions recorded.")
    for version in versions:
        print(describe_version(version))


def command_project_repo_add(arguments: argparse.Namespace, *, container) -> None:
    arguments.id = container.projects().repository(arguments.id, arguments.url, remove=False)
    console.print(f"added repository {arguments.url} to {arguments.id}", markup=False)


def command_project_repo_remove(arguments: argparse.Namespace, *, container) -> None:
    arguments.id = container.projects().repository(arguments.id, arguments.url, remove=True)
    console.print(f"removed repository {arguments.url} from {arguments.id}", markup=False)


def command_project_restore(arguments: argparse.Namespace, *, container) -> None:
    placement, name, arguments.shutter = container.projects().restore(arguments.id, arguments.shutter)
    cleared = f"; {arguments.shutter} moved to the storehouse" if arguments.shutter else ""
    console.print(f"restored {placement.project_id} {name} to floor {placement.floor}{cleared}", markup=False)


def observed_labels(registry: projects.Registry, hosts: list[Host], *, container) -> tuple[list[tuple[str, str, str]], list[str]]:
    return container.projects().observed_labels(registry, hosts)


def command_project_list(arguments: argparse.Namespace, *, container) -> None:
    registry = container.initialized_workspace().registry()
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
    suggestions, errors = container.projects().suggestions(registry)
    if suggestions:
        console.print("\n[bold]suggested links[/] (repository matches)")
    for link, project_id in suggestions:
        target = f"{link.host}:{link.label}"
        console.print(f"  {escape(target)} → {project_id} {escape(registry.get(project_id).name)}"
                      f"  [dim]fleet project link {project_id} {escape(shlex.quote(target))}[/]")
    for error in errors:
        console.print(f"[yellow]no suggestions from {escape(error)}[/]")

def command_building_capacity(arguments: argparse.Namespace, *, container) -> None:
    """The building's floors: a deliberate setting, never raised as a side effect of starting work (ADR 0005)."""
    workspace = container.initialized_workspace()
    if arguments.floors is None:
        console.print(f"{workspace.capacity()} floors")
        return
    workspace.set_capacity(arguments.floors)
    console.print(f"the building has {arguments.floors} floors")


def merge_detected(output: str) -> dict[str, str | None]:
    return parse_detected(output)


def command_install(arguments: argparse.Namespace, *, container) -> None:
    host, report, agent_socket = container.hosts().install(arguments.name)
    console.print(f"[bold]{host.name}[/] ({report['host']}) installed")
    for name in ("claude", "codex"):
        console.print(f"  {name}: {report['config'].get(name) or '[red]not found[/]'}")
    console.print(f"  ssh-agent for jobs: {agent_socket or '[yellow]none — jobs get no SSH_AUTH_SOCK[/]'}")
    if not report["tmux"]:
        console.print("  [red]tmux not found — jobs cannot start[/]")


def command_hooks(arguments: argparse.Namespace, *, container) -> None:
    host, report = container.hosts().hooks(arguments.name, arguments.action)
    events = ", ".join(report["events"]) or "none"
    console.print(f"[bold]{host.name}[/] ({report['host']}): {report['settings']} — fleet hooks now on: {events}")


def command_unlock(arguments: argparse.Namespace, *, container) -> None:
    """Add the host's key to its fleet ssh-agent; prompts for the passphrase once per boot."""
    if not sys.stdin.isatty():
        raise FleetError("unlock needs a real terminal to read the passphrase — run it in your own shell, "
                         "not via Claude Code's ! prefix")
    sys.exit(container.hosts().unlock(arguments.name, arguments.key))


def command_web(arguments: argparse.Namespace, *, container) -> None:
    load_command('web')(arguments, container)


def nonnegative_depth(value: str) -> int:
    depth = int(value)
    if depth < 0:
        raise argparse.ArgumentTypeError("depth must be nonnegative")
    return depth


def command_work_show(arguments: argparse.Namespace, *, container) -> None:
    item = container.resolved_work_detail(reference=arguments.id)
    parents = item["parents"]
    if arguments.json:
        print(json.dumps({**item, "parents": parents}, default=str))
        return
    print("Parent chain: " + (" > ".join(f"{p['title']} ({p['id']})" for p in parents)
                              if parents else "none (root item)"))
    print_status_item({**item, "children": []})
    print("Children:")
    if not item["children"]:
        print("  None recorded.")
    for child in item["children"]:
        print(f"  {child['kind']} {child['id']}: {child['title']} ({child['condition']})")


def command_status(arguments: argparse.Namespace, *, container) -> None:
    project = container.initialized_workspace().resolve_project(arguments.project)
    container.initialized_attention()
    projection = container.project_status(project=project)
    try:
        projection = filter_status(projection, item=work_cli_id(arguments.item, container=container) if arguments.item is not None else None,
                                   depth=arguments.depth, only_open=arguments.open)
    except LookupError as error:
        raise FleetError(str(error)) from error
    if arguments.json:
        print(json.dumps(projection))
        return
    print(f"Project: {projection['project']}")
    if not projection["work_items"]:
        print("No work items match the filters." if arguments.item or arguments.open
              else "No work items recorded.")
    for item in projection["work_items"]:
        print_status_item(item)
    if projection["attention"]:
        print("Unlinked attention:")
    for entry in projection["attention"]:
        print(f"  Attention {entry['id'][:8]} ({entry['kind']}): {entry['headline']}")


def print_status_item(item: dict[str, Any], depth: int = 0) -> None:
    indent = "  " * depth
    print(f"{indent}{item['kind']} {item['id'][:8]}: {item['title']}")
    print(f"{indent}  Goal: {item['goal']}")
    progress = item["progress"]
    mark = ("unknown" if progress["basis"] == "unknown" else
            f"{progress['complete']}/{progress['total']} {progress['basis']}")
    print(f"{indent}  Progress: {mark}; condition: {item['condition']}")
    if item['kind'] == 'milestone':
        print(f"{indent}  Interruptions: {item['interruptions']}")
    next_step = "not recorded" if item["next_step"] is None else item["next_step"]
    print(f"{indent}  Next step: {next_step}")
    for relation in item["relations"]:
        other = relation["id"] if relation["title"] is None else f"{relation['title']} ({relation['id']})"
        print(f"{indent}  {relation['type'].replace('-', ' ').capitalize()}: {other}")
    if item["plan"] is not None:
        print(f"{indent}  Plan: {item['plan'].splitlines()[0]}" + (" …" if "\n" in item["plan"].strip() else ""))
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
        attached = "none attached" if run["guidance"] is None else describe_guidance(run["guidance"])
        print(f"{indent}      Guidance: {attached}")
    print(f"{indent}  Library:")
    for entry in item["library"]:
        title = "unknown" if entry["title"] is None else entry["title"]
        print(f"{indent}    {entry['kind']}: {title} ({entry['availability']}) — {entry['canonical_location']}")
    if item["resume_condition"] is not None:
        print(f"{indent}  Resume condition: {item['resume_condition']}")
    for criterion in item["criteria"]:
        print(f"{indent}  Criterion {criterion['id'][:8]} ({criterion['verification']}, {criterion['state']}): {criterion['text']}")
    for entry in item["attention"]:
        print(f"{indent}  Attention {entry['id'][:8]} ({entry['kind']}): {entry['headline']}")
    for decision in item["decisions"]:
        print(f"{indent}  Decision {decision['id']}: {decision['question']}")
        print(f"{indent}    {decision['answer']} — {decision['actor']} at {decision['time']}")
        print(f"{indent}    Principle: {'unknown' if decision['principle'] is None else decision['principle']}")
    summary = item["summary"]
    if summary is not None:
        print(f"{indent}  Summary ({summary['authoring_role']}, {summary['updated']}):")
        for field in ("purpose", "done", "doing", "next"):
            print(f"{indent}    {field.capitalize()}: {summary[field]}")
    for child in item["children"]:
        print_status_item(child, depth + 1)


def resolve_cli_id(reference: str, identities: list[str], kind: str) -> str:
    from fleet.identifiers import resolve_prefix
    return resolve_prefix(reference, identities, kind)


def work_cli_id(reference: str, *, container) -> str:
    return container.references().work(reference)


def located_context(reference: str, *, container) -> str:
    value, warn = container.context().locate(reference)
    if warn:
        error_console.print(f"fleet: context reference '{reference}' is not a file here; the deck will show it as "
                            "text only. Give an absolute path or fleet://<host>/<path>.", style="yellow", markup=False,
                            soft_wrap=True)
    return value


def attention_cli_id(reference: str, *, container) -> str:
    return container.references().attention_item(reference)


def resolve_step_work_ids(steps: list[dict[str, Any]], *, container) -> None:
    container.references().step_work(steps)


def command_work(arguments: argparse.Namespace, *, container) -> None:
    data = vars(arguments).copy()
    command = data.pop('work_operation')
    for name in ('handler', 'command'):
        data.pop(name, None)
    item, known = container.work_commands().execute(command, data)
    console.print_json(json.dumps(asdict(item), default=str))
    if command in ("add", "set") and known is not None and item.kind not in known:
        error_console.print(f"new kind '{item.kind}' added to project {item.project}'s kinds "
                            f"(known: {', '.join(known)})", markup=False)


KIND_HELP = (f"{', '.join(KINDS)}, or a new label, which becomes one of the project's kinds; default task")


def add_work_parsers(commands) -> None:
    work = commands.add_parser("work", help="persistent work items").add_subparsers(required=True)
    show = work.add_parser("show", help="read a work item and its linked records")
    show.add_argument("id", help="work item ID or unique prefix")
    show.add_argument("--json", action="store_true", help="emit the full detail record")
    show.set_defaults(handler=command_work_show)
    work_help = {"add": "record a new work item in a project", "set": "change a work item's fields",
                 "move": "put a work item under another parent, or at the root",
                 "relate": "record that a work item depends on or relates to another",
                 "ready": "move waiting work to 'ready for review'"}
    for name in ("add", "set", "move", "relate", "ready"):
        action = work.add_parser(name, help=work_help[name])
        action.set_defaults(handler=command_work, work_operation=name)
        action.add_argument("--actor", required=True)
        action.add_argument("title" if name == "add" else "id")
        if name == "add":
            add_project_argument(action, "--project", required=True)
            action.add_argument("--goal", required=True)
            action.add_argument("--kind", default="task", help=KIND_HELP)
            for field in ("parent", "focus", "next-step", "plan"):
                action.add_argument(f"--{field}")
        elif name == "set":
            action.add_argument("--kind", default=argparse.SUPPRESS, help=KIND_HELP)
            # Checked by the domain, whose error quotes the values: argparse would list "ready for review" bare
            action.add_argument("--condition", default=argparse.SUPPRESS,
                                help="one of: " + ", ".join(f"'{condition}'" for condition in CONDITIONS))
            for field in ("title", "goal", "resume-condition", "next-step", "focus", "plan"):
                action.add_argument(f"--{field}", default=argparse.SUPPRESS)
        elif name == "move":
            parent = action.add_mutually_exclusive_group(required=True)
            parent.add_argument("--parent")
            parent.add_argument("--root", dest="parent", action="store_const", const=None)
        elif name == "relate":
            action.add_argument("to_item")
            action.add_argument("--type", default="depends-on", choices=RELATION_TYPES)
    criterion = commands.add_parser("criterion", help="work completion criteria").add_subparsers(required=True)
    add = criterion.add_parser("add", help="add a completion criterion to a work item")
    add.set_defaults(handler=command_work, work_operation="criterion_add")
    add.add_argument("id", help="work item ID")
    add.add_argument("text")
    add.add_argument("--verification", required=True, choices=("checked", "judged", "accepted"))
    add.add_argument("--evidence-reference", help="absolute path to recorded local evidence")
    add.add_argument("--required-result", help="required result field in JSON evidence")
    add.add_argument("--actor", required=True)
    meet = criterion.add_parser("meet", help="mark a criterion met; there is no un-meet")
    meet.set_defaults(handler=command_work, work_operation="meet")
    meet.add_argument("id", help="criterion ID")
    meet.add_argument("--evidence", action="append", default=[])
    meet.add_argument("--actor", required=True)
    summary = commands.add_parser("summary", help="work summaries").add_subparsers(required=True)
    action = summary.add_parser("set", help="record a new version of a work item's summary (all fields)")
    action.set_defaults(handler=command_work, work_operation="set_summary")
    action.add_argument("id", help="work item ID")
    for field in ("purpose", "done", "doing", "next", "authoring-role", "actor"):
        action.add_argument(f"--{field}", required=True)


def describe_guidance(guidance: dict) -> str:
    return ", ".join(f"{name} version {guidance[name]['version']}" for name in ("constitution", "charter")
                     if guidance[name] is not None)


def job_run(job: str, *, container) -> str | None:
    return container.decision_commands().job_run(job)


def hand_decision_to_job(job: str, arguments: argparse.Namespace, *, container) -> str:
    return container.decision_commands().hand_to_job(job, {name: getattr(arguments, name)
        for name in ('work_item', 'question', 'answer', 'principle', 'actor', 'context')})


def command_decision_record(arguments: argparse.Namespace, *, container) -> None:
    decision, identity, job = container.decision_commands().record(**{name: getattr(arguments, name)
        for name in ('work_item', 'question', 'answer', 'principle', 'actor', 'context', 'run')})
    if decision is None:
        print(f"Decision {identity} handed to the controller via job {job}'s stream; "
              f"it is recorded there when the controller next hears from this host.")
    else:
        print(json.dumps(asdict(decision), default=lambda value: value.isoformat()))


def command_decision_list(arguments: argparse.Namespace, *, container) -> None:
    log = container.decision_commands().listing(epic=arguments.epic, project=arguments.project)
    if arguments.json:
        print(json.dumps(log, default=str))
        return
    if not log:
        print("No decisions recorded.")
    for entry in log:
        items = ", ".join(item["id"] if item["title"] is None else f"{item['title']} ({item['id']})"
                          for item in entry["work_items"])
        print(f"{entry['time']} {entry['actor']} on {items}")
        print(f"  Question: {entry['question']}")
        print(f"  Answer: {entry['answer']}")
        print(f"  Principle: {'unknown' if entry['principle'] is None else entry['principle']}")
        print(f"  Guidance: {'unknown' if entry['guidance'] is None else describe_guidance(entry['guidance'])}")


def history_cutoff(text: str) -> datetime:
    try:
        return parse_moment(text)
    except ValueError as error:
        raise argparse.ArgumentTypeError(str(error)) from error


def history_since(text: str) -> datetime:
    try:
        return parse_since(text)
    except ValueError as error:
        raise argparse.ArgumentTypeError(str(error)) from error


def history_value(value: object) -> str:
    if value is None:
        return "—"
    text = value if isinstance(value, str) else json.dumps(value, sort_keys=True)
    text = " ".join(text.split())
    return text if len(text) <= 100 else text[:99] + "…"


def history_source(entry: dict[str, Any]) -> str:
    if entry["source_run"] is not None:
        return f"run {entry['source_run'][:8]}"
    if entry["job"] is not None:
        return f"job {entry['job'][:8]}"
    return "no recorded run"


def command_history(arguments: argparse.Namespace, *, container) -> None:
    if arguments.subject is None:
        raise FleetError("give --subject (a work item, attention item, project, run or other id or id prefix), "
                         "or a subcommand such as prune")
    try:
        history = container.subject_history(reference=arguments.subject, since=arguments.since)
    except LookupError as error:
        raise FleetError(str(error)) from error
    if arguments.json:
        print(json.dumps(history))
        return
    print(f"History of {history['kind']} {history['id']}, newest first")
    if not history["entries"]:
        print("No changes recorded" + (" in that period." if arguments.since is not None else "."))
    for entry in history["entries"]:
        about = "" if (entry["kind"], entry["id"]) == (history["kind"], history["id"]) \
            else f" ({entry['kind']} {entry['id'][:8]})"
        time_text = datetime.fromisoformat(entry["time"]).strftime("%Y-%m-%d %H:%M:%S %Z")
        print(f"{time_text}  {entry['actor']}  {history_source(entry)}{about}")
        if not entry["changes"]:
            print("  no field changed")
        for change in entry["changes"]:
            print(f"  {change['field']}: {history_value(change['before'])} → {history_value(change['after'])}")


def command_history_prune(arguments: argparse.Namespace, *, container) -> None:
    history = container.history()
    count, oldest, newest = history.span_before(arguments.before)
    if not count:
        print(f"No history entries before {arguments.before.isoformat()}; nothing to delete.")
        return
    if not arguments.yes:
        print(f"Would delete {count} history entries, from {oldest} to {newest} (everything before "
              f"{arguments.before.isoformat()}). Who changed what in that period would no longer be readable.")
        raise FleetError("nothing deleted; run again with --yes to delete them")
    deleted = history.prune(arguments.before, actor=arguments.actor)
    print(f"Deleted {deleted} history entries from {oldest} to {newest}; "
          f"one entry by {arguments.actor} records the pruning.")


def command_answer(arguments: argparse.Namespace, *, container) -> None:
    result = container.attention_commands().answer(arguments.id, arguments.answer, actor=arguments.actor,
                                               next_step=arguments.next_step)
    console.print_json(json.dumps(result if isinstance(result, dict) else asdict(result),
                                  default=lambda value: value.isoformat()))


UNTIL_EXAMPLE = "2026-10-02T09:00:00+00:00"


def command_attention(arguments: argparse.Namespace, *, container) -> None:
    command = arguments.attention_command
    data = vars(arguments).copy()
    for name in ('handler', 'command', 'attention_command', 'json'):
        data.pop(name, None)
    if command == 'add':
        data['context_reference'] = located_context(data['context_reference'], container=container)
    if command == 'snooze':
        try:
            data['until'] = datetime.fromisoformat(data['until'])
        except ValueError:
            raise FleetError(f"--until takes a timezone-aware ISO timestamp, e.g. {UNTIL_EXAMPLE}") from None
    item = container.attention_commands().execute(command, data)
    if command == 'list':
        console.print_json(json.dumps([asdict(entry) for entry in item], default=str))
    elif command in ('delegate', 'take') and not getattr(arguments, 'json', False):
        print(f"{item.id}: " + ('Delegated; stays open under With agent. Take it back with fleet attention take ' + item.id + ' --actor ACTOR'
              if command == 'delegate' else 'Taken back; agent authority for this item is revoked. The triage process may continue for other items.'))
    else:
        console.print_json(json.dumps(asdict(item), default=str))


DEFAULT_PERMISSION_HELP = "permission acceptEdits (claude) or workspace-write (codex) unless --permission says otherwise"


def default_actor() -> str:
    return actor_identity()


def add_actor_option(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--actor", default=default_actor(),
                        help="who the store records as doing this (default: job:$FLEET_JOB_ID inside a fleet job, "
                             "else user)")


COMMAND_GROUPS = {
    "Jobs": ("send", "dispatch", "start", "add", "push", "pull", "ls", "watch", "show", "tail", "attach", "wait", "result",
             "cancel", "mv", "rm", "notify", "run"),
    "Work": ("status", "work", "criterion", "summary", "attention", "answer", "decision", "guidance", "library",
             "history", "triage", "store"),
    "Projects": ("project", "building", "libraries", "web"),
    "Hosts": ("hosts", "host", "install", "hooks", "unlock"),
    "Agent-internal": ("orchestrate", "control"),
}
QUICK_START = """\
quick start:
  fleet host add carbon --local && fleet install carbon
  fleet project add Demo --link carbon:demo
  fleet send -H carbon -p demo -C ~/src/demo -d "Fix tests" -s "Fix the failing tests"
"""


def group_commands(parser: argparse.ArgumentParser, commands: argparse._SubParsersAction) -> None:
    """Replace argparse's flat command list with COMMAND_GROUPS, each command with its help line."""
    helps = {choice.dest: choice.help for choice in commands._choices_actions}
    width = max(len(name) for name in helps)
    sections = [f"{group}:\n" + "\n".join(f"  {name:<{width}}  {helps[name]}" for name in names)
                for group, names in COMMAND_GROUPS.items()]
    commands._choices_actions = []
    commands.metavar = "COMMAND"
    commands.help = "one of the commands below; fleet COMMAND --help for its options"
    parser.epilog = "\n\n".join(sections) + "\n\n" + QUICK_START


PROJECT_HELP = "project (ID, prefix or name)"


def add_project_argument(parser: argparse.ArgumentParser, *flags: str, **options: Any) -> None:
    detail = options.pop("help", None)
    parser.add_argument(*flags, help=PROJECT_HELP + (f"; {detail}" if detail else ""), **options)


def add_listing_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--host", action="append", help="only these hosts (repeatable)")
    add_project_argument(parser, "--project", "-p", help="only this project")
    parser.add_argument("--by", dest="group_by", choices=("project", "host"), default="project")
    parser.add_argument("--all", "-a", action="store_true", help="include old finished jobs")
    parser.add_argument("--since", type=float, default=24, help="hours of finished jobs to show (default 24)")
    parser.add_argument("--brief", "-b", action="store_true", help="hide step lists")
    parser.add_argument("--no-sessions", dest="sessions", action="store_false",
                        help="leave out live interactive Claude/Codex sessions")


class StepWorkItem(argparse.Action):
    """--step-work-item ID: the --step just before it serves that work item rather than the job's."""

    def __call__(self, parser, namespace, value, option_string=None) -> None:
        steps = namespace.step or []
        named = dict(namespace.step_work_items or {})
        if not steps or len(steps) - 1 in named:
            parser.error(f"{option_string} must follow the --step it names, once per step")
        named[len(steps) - 1] = value
        namespace.step_work_items = named


def add_step_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--step", "-s", action="append", help="a task prompt; repeat for a task list")
    parser.add_argument("--step-work-item", dest="step_work_items", action=StepWorkItem, metavar="ID",
                        help="the work item the preceding --step serves, when not the job's own")
    parser.add_argument("--steps-file", "-f",
                        help='markdown list (one step per item) or JSON list of prompts or '
                             '{"prompt", "title"?, "work_item"?} objects')
    parser.add_argument("--context", "-c", action="append", help="file/dir to copy into the job's context dir")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="fleet", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    commands = parser.add_subparsers(dest="command", required=True)

    store_commands = commands.add_parser("store", help="local storage accounting").add_subparsers(dest="store_command", required=True)
    usage = store_commands.add_parser("usage", help="database rows and retained documents and traces")
    usage.add_argument("--json", action="store_true")
    usage.set_defaults(handler=command_store_usage)

    listing = commands.add_parser("ls", help="list jobs across hosts, grouped by project")
    add_listing_options(listing)
    listing.add_argument("--json", action="store_true")
    listing.set_defaults(handler=command_list)

    watch = commands.add_parser("watch", help="live-updating ls")
    add_listing_options(watch)
    watch.add_argument("--interval", "-n", type=float, default=3)
    watch.set_defaults(handler=command_watch)

    send = commands.add_parser(
        "send", help="start a job (a task list): starts an agent on the host",
        description=f"Starts a Claude or Codex agent on HOST in CWD that works through the steps, with "
                    f"{DEFAULT_PERMISSION_HELP}. Copies the -c paths into the job's context dir and, for a "
                    f"--work-item, the project's constitution and the epic's charter. Records the job as a run "
                    f"in the store. Stop it with fleet cancel.")
    send.add_argument("--host", "-H", required=True)
    add_project_argument(send, "--project", "-p", required=True, help="worker label comes from its host link")
    send.add_argument("--work-item", help="link the created job to stored work")
    send.add_argument("--description", "-d", required=True, help="one line: what this job is working on")
    send.add_argument("--agent", "--runtime", "-a", dest="agent", choices=("claude", "codex"), default="claude")
    send.add_argument("--cwd", "-C", required=True, help="working directory on the host")
    send.add_argument("--permission", help="claude: acceptEdits|bypassPermissions|plan|default; "
                                           "codex: read-only|workspace-write|danger-full-access; "
                                           "default acceptEdits / workspace-write")
    add_actor_option(send)
    send.add_argument("--model", "-m")
    send.add_argument("--allow", action="append",
                      help="claude permission rule to pre-approve, e.g. 'Bash(ss:*)' (repeatable)")
    send.add_argument("--add-dir", action="append", help="extra directory on the host the claude agent may use (repeatable)")
    send.add_argument("--env", action="append", help="NAME=value set in the agent's environment (repeatable)")
    send.add_argument("--id")
    send.add_argument("--keep-going", action="store_true", help="continue to next step after a failure")
    send.add_argument("--hold", action="store_true", help="create but don't start; start it with fleet start JOB")
    send.add_argument("--wait", "-w", action="store_true", help="block until the job finishes")
    send.add_argument("--json", action="store_true")
    add_step_options(send)
    send.set_defaults(handler=command_send)

    dispatch = commands.add_parser(
        "dispatch", help="send one instruction for a work item: claims it and starts an agent on the host",
        description=f"Claims the work item and starts a Claude or Codex agent on HOST in CWD with one step, the "
                    f"instruction, with {DEFAULT_PERMISSION_HELP}. Copies the project's constitution and the "
                    f"epic's charter into the job's context dir. The job's project is the work item's.")
    dispatch.add_argument("work_item")
    dispatch.add_argument("instruction")
    dispatch.add_argument("--host", "-H", required=True)
    dispatch.add_argument("--runtime", "--agent", "-a", dest="agent", choices=("claude", "codex"), required=True)
    dispatch.add_argument("--cwd", "-C", required=True)
    dispatch.add_argument("--permission", help="runtime permission, as for fleet send; "
                                               "default acceptEdits / workspace-write")
    dispatch.add_argument("--id")
    dispatch.add_argument("--json", action="store_true")
    add_actor_option(dispatch)
    dispatch.set_defaults(handler=command_dispatch_work, permission=None, model=None, allow=None,
                          add_dir=None, env=None, keep_going=False, hold=False, wait=False,
                          context=None, steps_file=None, step_work_items=None)

    triage = commands.add_parser('triage', help='inspect project triage policy and queue')
    triage_commands = triage.add_subparsers(dest='triage_command', required=True)
    triage_status = triage_commands.add_parser('status', help='mandate, queue, live run and daily budget')
    add_project_argument(triage_status, 'project')
    triage_status.add_argument('--json', action='store_true', help='machine-readable status')
    triage_status.set_defaults(handler=command_triage_status)
    policy = triage_commands.add_parser('policy', help='read or version the project triage mandate')
    policy_commands = policy.add_subparsers(dest='policy_command', required=True)
    policy_show = policy_commands.add_parser('show', help='show current policy and version')
    add_project_argument(policy_show, 'project')
    policy_show.add_argument('--json', action='store_true', help='machine-readable policy and version')
    policy_show.set_defaults(handler=command_triage_policy)
    policy_set = policy_commands.add_parser('set', help='validate and record a new policy; active runs retain their version')
    add_project_argument(policy_set, 'project')
    policy_set.add_argument('--file', required=True, help='triage mandate JSON file; explicit example and fields: docs/triage-policy.md')
    policy_set.add_argument('--actor', required=True, help='author recorded on this version')
    policy_set.set_defaults(handler=command_triage_policy)

    orchestrate = commands.add_parser('orchestrate', help='start an orchestrator agent on the controller, with write access to the store')
    orchestrate.add_argument('work_item')
    orchestrate.add_argument('--mandate', required=True, help='recorded mandate path')
    orchestrate.add_argument('--host', required=True, help='configured local controller host')
    orchestrate.add_argument('--runtime', dest='agent', choices=('claude', 'codex'), required=True)
    orchestrate.add_argument('--cwd', required=True)
    orchestrate.add_argument('--permission', help='runtime permission, as for fleet send')
    orchestrate.set_defaults(handler=command_orchestrate)
    control = commands.add_parser('control', help='agent-internal: an activated orchestrator changes the store')
    control.add_argument('activation')
    control.add_argument('operation', choices=('state', 'progress', 'meet', 'attention', 'dispatch', 'decide', 'summary', 'propose',
                                             'retry', 'add_step', 'grant', 'resolve', 'escalate', 'record_decision'))
    control.add_argument('payload', help='JSON object of command fields')
    control.set_defaults(handler=command_control)

    run = commands.add_parser("run", help="stored execution runs").add_subparsers(dest="run_command", required=True)
    show_run = run.add_parser("show", help="steps, git, documents and trace of a stored run")
    show_run.add_argument("id", help="run ID or an unambiguous prefix")
    show_run.add_argument("--json", action="store_true")
    show_run.set_defaults(handler=command_run_show)
    run_link = run.add_parser("link", help="link an existing host job without fetching it")
    run_link.add_argument("host")
    run_link.add_argument("job")
    run_link.add_argument("work_item")
    run_link.add_argument("--actor", default="user")
    run_link.set_defaults(handler=command_run_link)
    resolve_unknown = run.add_parser("resolve-unknown", help="explicitly close an unknown run to permit retry")
    resolve_unknown.add_argument("run")
    add_actor_option(resolve_unknown)
    resolve_unknown.set_defaults(handler=command_resolve_unknown)
    retry = run.add_parser(
        "retry", help="retry an action after its run has a known end: starts a new agent on the same host",
        description=f"Starts a new agent on the run's host, as a new job with the action's original steps, context "
                    f"and guidance, and its permission ({DEFAULT_PERMISSION_HELP} when the action named none). "
                    f"The new job is a new run of the same action.")
    retry.add_argument("run")
    add_actor_option(retry)
    retry.set_defaults(handler=command_run_retry)

    add = commands.add_parser("add", help="append steps to a job; if a step is blocked, they answer it",
                              description="Appends steps to a job and starts it again if it is idle. When a step "
                                          "is blocked waiting for an answer, the steps answer it instead: they "
                                          "run next and the step's attention item is resolved (--no-answer "
                                          "appends instead).")
    add.add_argument("job")
    add.add_argument("--retry", action="store_true", help="also re-queue failed/blocked/cancelled steps")
    add.add_argument("--no-answer", action="store_true",
                     help="append even when a blocked step waits for an answer (by default the steps answer it)")
    add_actor_option(add)
    add_step_options(add)
    add.set_defaults(handler=command_add)

    start = commands.add_parser("start", help="start a job sent with --hold: starts its agent on the host")
    start.add_argument("job")
    start.set_defaults(handler=command_start)

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
    add_project_argument(move, "project")
    move.set_defaults(handler=command_move)

    remove = commands.add_parser(
        "rm", help="delete a finished job's directory on its host, including outbox and results; cannot be undone",
        description="Delete a job's directory on its host, including its outbox and step results. This cannot be "
                    "undone. Done, failed, cancelled and lost jobs are removed; queued, blocked and stalled jobs "
                    "need --force; running jobs must be cancelled first.")
    remove.add_argument("job")
    remove.add_argument("--force", action="store_true",
                        help="also remove a queued, blocked or stalled job, losing its pending work or question")
    remove.set_defaults(handler=command_remove)

    notify = commands.add_parser("notify", help="stream one line per status change (for monitors)")
    notify.add_argument("--host", action="append")
    notify.add_argument("--interval", "-n", type=float, default=5)
    notify.set_defaults(handler=command_notify)

    hosts = commands.add_parser("hosts", help="configured hosts and reachability")
    hosts.set_defaults(handler=command_hosts)

    host = commands.add_parser("host", help="manage hosts").add_subparsers(dest="host_command", required=True)
    host_add = host.add_parser("add", help="add a host to config.json (replaces one of the same name)")
    host_add.add_argument("name")
    host_add.add_argument("--ssh", help="ssh target (default: the name)")
    host_add.add_argument("--local", action="store_true", help="this machine, no ssh")
    host_add.add_argument("--python", default="python3")
    host_add.set_defaults(handler=command_host_add)
    host_remove = host.add_parser("rm", help="remove a host from config.json; its jobs stay on the host")
    host_remove.add_argument("name")
    host_remove.set_defaults(handler=command_host_remove)

    libraries = commands.add_parser("libraries", help="configured local project libraries")
    libraries.set_defaults(handler=command_libraries)
    library = commands.add_parser("library", help="manage local project libraries").add_subparsers(
        dest="library_command", required=True)
    library_link = library.add_parser("link", help="index an external URL; grants no access")
    library_link.add_argument("url")
    add_project_argument(library_link, "--project", help="required when no work item is supplied")
    library_link.add_argument("--work-item")
    library_link.add_argument("--title", help="optional display label; omitted titles remain unknown")
    library_link.add_argument("--actor", default="user")
    library_link.set_defaults(handler=command_library_link)
    library_add = library.add_parser("add", help="add a local folder to config.json as a library root")
    add_project_argument(library_add, "project")
    library_add.add_argument("path")
    library_add.add_argument("--recursive", action="store_true",
                             help="read Markdown in every folder, not only the top level and docs/")
    library_add.set_defaults(handler=command_library_add)
    library_remove = library.add_parser("rm", help="remove a library root from config.json; files stay")
    add_project_argument(library_remove, "project")
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
    add_project_argument(project_rename, "id")
    project_rename.add_argument("name")
    project_rename.set_defaults(handler=command_project_rename)
    project_link = project.add_parser("link", help="attach a host's label to a project")
    add_project_argument(project_link, "id")
    project_link.add_argument("link", metavar="HOST:LABEL")
    project_link.set_defaults(handler=command_project_link)
    project_unlink = project.add_parser("unlink", help="detach a host's label from its project")
    project_unlink.add_argument("link", metavar="HOST:LABEL")
    project_unlink.set_defaults(handler=command_project_unlink)
    project_merge = project.add_parser("merge", help="irreversibly delete OTHER and move its records, links and repositories into KEEP",
                                      description="Permanently delete OTHER, free its floor and move its work, attention, runs, decisions, links and repositories into KEEP. This cannot be undone.")
    add_project_argument(project_merge, "keep", metavar="KEEP-ID", help="the older project: keeps its ID and name")
    add_project_argument(project_merge, "other", metavar="OTHER-ID", help="permanently deleted after its records, links and repositories move to KEEP")
    project_merge.set_defaults(handler=command_project_merge)
    project_repo = project.add_parser("repo", help="repository remotes used to suggest links").add_subparsers(
        dest="project_repo_command", required=True)
    project_repo_add = project_repo.add_parser("add", help="record a repository remote for a project")
    add_project_argument(project_repo_add, "id")
    project_repo_add.add_argument("url")
    project_repo_add.set_defaults(handler=command_project_repo_add)
    project_repo_remove = project_repo.add_parser("rm", help="forget a repository remote for a project")
    add_project_argument(project_repo_remove, "id")
    project_repo_remove.add_argument("url")
    project_repo_remove.set_defaults(handler=command_project_repo_remove)

    management = project.add_parser(
        'management', help="register a project's management Git repository; permanent",
        description="Registers PATH, the root of an existing Git working tree, as the project's management "
                    "repository and moves its summaries out of the store into it. This is permanent: it cannot be "
                    "changed or undone. Optional: a project's first record creates one under fleet's home.")
    add_project_argument(management, 'id')
    management.add_argument('path', help='root of an existing Git working tree')
    add_actor_option(management)
    project_restore = project.add_parser("restore", help="bring a shuttered project back from the storehouse to a floor")
    add_project_argument(project_restore, "id")
    add_project_argument(project_restore, "--shutter", metavar="OTHER-ID",
                                 help="when no floor is free, move this project to the storehouse to make room")
    project_restore.set_defaults(handler=command_project_restore)
    management.set_defaults(handler=command_project_management)

    guidance = commands.add_parser(
        "guidance", help="a project's constitution and its epics' charters, versioned in the management repository"
    ).add_subparsers(dest="guidance_command", required=True)
    subject_help = PROJECT_HELP + " (its constitution) or an epic ID (its charter)"
    guidance_show = guidance.add_parser("show", help="print the current or a numbered version")
    guidance_show.add_argument("subject", help=subject_help)
    guidance_show.add_argument("--version", type=int, help="an older version number from history")
    guidance_show.add_argument("--json", action="store_true")
    guidance_show.set_defaults(handler=command_guidance_show)
    guidance_edit = guidance.add_parser("edit", help="record a new version from a file or stdin")
    guidance_edit.add_argument("subject", help=subject_help)
    guidance_edit.add_argument("--file", help="Markdown file; stdin when omitted")
    guidance_edit.add_argument("--actor", required=True)
    guidance_edit.add_argument("--run", help="the run that wrote this version")
    guidance_edit.set_defaults(handler=command_guidance_edit)
    guidance_promote = guidance.add_parser("promote", help="add a decision to its epic charter's decisions in force")
    guidance_promote.add_argument("decision", help="decision ID")
    guidance_promote.add_argument("--epic", required=True)
    guidance_promote.add_argument("--actor", required=True)
    guidance_promote.set_defaults(handler=command_guidance_promote)
    guidance_history = guidance.add_parser("history", help="versions, newest first")
    guidance_history.add_argument("subject", help=subject_help)
    guidance_history.add_argument("--json", action="store_true")
    guidance_history.set_defaults(handler=command_guidance_history)

    add_work_parsers(commands)

    status = commands.add_parser("status", help="persisted project work and open attention")
    add_project_argument(status, "project")
    status.add_argument("--json", action="store_true", help="emit the project projection")
    status.add_argument("--item", help="only this work item (ID or unique prefix) and descendants")
    status.add_argument("--depth", type=nonnegative_depth, help="maximum child depth; 0 shows roots only")
    status.add_argument("--open", action="store_true", help="hide complete work and resolved attention")
    status.set_defaults(handler=command_status)

    decision = commands.add_parser("decision", help="decisions agents made under guidance").add_subparsers(
        dest="decision_command", required=True)
    decision_record = decision.add_parser("record", help="record a decision and the principle it relied on")
    decision_record.add_argument("--work-item", required=True)
    decision_record.add_argument("--question", required=True)
    decision_record.add_argument("--answer", required=True)
    decision_record.add_argument("--principle", required=True,
                                 help='the rule relied on, e.g. "Constitution: decide yourself — test-only fixes"')
    decision_record.add_argument("--actor", required=True)
    decision_record.add_argument("--context", default="", help="where the question arose")
    decision_record.add_argument("--run", help="the run deciding; FLEET_JOB_ID's run when omitted")
    decision_record.set_defaults(handler=command_decision_record)
    decision_list = decision.add_parser("list", help="decisions on a project's or an epic's work, newest first")
    decision_scope = decision_list.add_mutually_exclusive_group(required=True)
    add_project_argument(decision_scope, "--project")
    decision_scope.add_argument("--epic")
    decision_list.add_argument("--json", action="store_true")
    decision_list.set_defaults(handler=command_decision_list)

    history = commands.add_parser("history", help="the audit trail: who changed what, and when")
    history.add_argument("--subject", help="a work item, attention item, project, run or other id; "
                                           "a unique id prefix or a subject such as attention:<id> also works")
    history.add_argument("--since", type=history_since, help="an ISO date or time, or an age such as 12h or 7d")
    history.add_argument("--json", action="store_true")
    history.set_defaults(handler=command_history)
    history_commands = history.add_subparsers(dest="history_command")
    runs = history_commands.add_parser("runs", help="stored runs, newest first")
    for name in ("project", "work-item", "host", "status", "kind", "since", "until"):
        runs.add_argument(f"--{name}", help=PROJECT_HELP if name == "project" else None)
    runs.add_argument("--limit", type=int, default=50)
    for name in ("descendants", "unlinked", "json"):
        runs.add_argument(f"--{name}", action="store_true")
    runs.set_defaults(handler=command_history_runs)

    history_prune = history_commands.add_parser(
        "prune", help="delete entries older than a date; shows what it would delete unless --yes")
    history_prune.add_argument("--before", required=True, type=history_cutoff,
                               help="an ISO date or time (UTC unless it names a zone)")
    history_prune.add_argument("--yes", action="store_true", help="delete; without it nothing is deleted")
    history_prune.add_argument("--actor", default="user")
    history_prune.set_defaults(handler=command_history_prune)

    answer = commands.add_parser(
        "answer", help="record an answer as a decision; continue a blocked job step when applicable",
        description="For a blocked job step, adds the answer as the job's next step on its host and resolves the "
                    "item, recording the answer as a decision. For any other item, records the answer as a decision "
                    "and resolves the item. Neither "
                    "can be undone.")
    answer.add_argument("id", help="attention item ID (fleet attention list)")
    answer.add_argument("answer", help="the reply; for an item with options, an option's 1-based number or text")
    answer.add_argument("--next-step", help="also set the work item's next step (decisions only)")
    add_actor_option(answer)
    answer.set_defaults(handler=command_answer)

    attention = commands.add_parser("attention", help="stored questions, blockers and alerts").add_subparsers(
        dest="attention_command", required=True)
    attention_add = attention.add_parser("add", help="raise an attention item")
    attention_add.add_argument("headline")
    attention_add.add_argument("--kind", required=True, choices=attention_module.KINDS)
    for field in ("project", "source", "source-reference", "context-reference", "actor"):
        attention_add.add_argument(f"--{field}", required=True,
                                   help=PROJECT_HELP if field == "project" else None)
    attention_add.add_argument("--owner", required=True, choices=("agent", "user"),
                               help="who must act: user puts it in 'need you'; agent leaves it to the project's agent")
    attention_add.add_argument("--reason", help="why the owner must act, shown with the item "
                                                "(for --owner user: why an agent cannot decide it)")
    attention_add.add_argument("--work-item")
    attention_add.add_argument("--run")
    attention_add.set_defaults(handler=command_attention)
    attention_list = attention.add_parser("list", help="attention items as JSON, with their IDs")
    add_project_argument(attention_list, "--project")
    attention_list.add_argument("--all", action="store_true", help="include resolved items (default: unresolved)")
    attention_list.add_argument("--state", choices=("open", "acknowledged", "snoozed", "resolved"))
    attention_list.add_argument("--owner", choices=("agent", "user"), help="only items this owner must act on")
    attention_list.set_defaults(handler=command_attention)
    delegate = attention.add_parser(
        "delegate", help="hand your item to the project's agent under a confirmed triage policy; it stays open and listed, and you can take it back")
    take = attention.add_parser("take", help="take an item back from the agent; revokes authority for this item, while the triage process may continue for other items")
    delegate.description = "Requires a confirmed triage policy. The item stays open under With agent; take it back to revoke item authority. Session questions remain terminal-only."
    take.description = "Revokes agent authority for this item. The triage process may continue handling other items. Delegate again under a confirmed policy to return ownership."
    escalate = attention.add_parser("escalate", help="(agents) hand an agent's item to the user, saying why")
    for action in (delegate, take, escalate):
        action.add_argument("id")
        action.add_argument("--actor", required=True)
        action.set_defaults(handler=command_attention, note=None, reason=None)
    delegate.add_argument("--json", action="store_true", help="machine-readable resulting item")
    take.add_argument("--json", action="store_true", help="machine-readable resulting item")
    delegate.add_argument("--note", help="what you want the agent to do, kept with the item")
    take.add_argument("--reason", help="why you are taking it back, kept with the item")
    escalate.add_argument("--reason", required=True, help="why the user must decide it")
    attention_help = {"ack": "acknowledge an item; it stays open",
                      "snooze": "hide an item until a time; it stays open",
                      "resolve": "close an item for good; does not answer it or unblock its job"}
    for name in ("ack", "snooze", "resolve"):
        action = attention.add_parser(name, help=attention_help[name])
        action.add_argument("id")
        action.add_argument("--actor", required=True)
        if name == "snooze":
            action.add_argument("--until", required=True, help=f"timezone-aware ISO timestamp, e.g. {UNTIL_EXAMPLE}")
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

    hooks = commands.add_parser("hooks", help="raise attention items from interactive Claude sessions on a host")
    hooks.add_argument("action", choices=("install", "uninstall"),
                       help="merge fleet's hooks into the host's ~/.claude/settings.json, or remove only them")
    hooks.add_argument("name", help="the host")
    hooks.set_defaults(handler=command_hooks)

    unlock = commands.add_parser("unlock", help="add a key to the host's fleet ssh-agent (passphrase once per boot)")
    unlock.add_argument("name")
    unlock.add_argument("--key", help="key path on the host (default: ssh-add's defaults)")
    unlock.set_defaults(handler=command_unlock)

    web = commands.add_parser("web", help="serve the deck, the web dashboard")
    web.add_argument("--host", action="append")
    web.add_argument("--port", type=int, default=8787)
    web.add_argument("--bind", default="127.0.0.1")
    web.add_argument("--open", action="store_true", help="open a browser tab")
    web.add_argument("--fixture", help=argparse.SUPPRESS)  # serve a recorded fleet from JSON, for tests
    web.set_defaults(handler=command_web)
    group_commands(parser, commands)
    return parser


def main(argv: list[str] | None = None, *, container=None) -> None:
    arguments = build_parser().parse_args(argv)
    container = Container() if container is None else container
    try:
        for message in validate_paths(arguments.command):
            error_console.print(message, markup=False)
        container.store()
        # Worker decisions may belong to a controller's store, so resolve those only
        # after command_decision_record has determined where the write belongs.
        if (getattr(arguments, "work_item", None) is not None
                and arguments.handler not in (command_decision_record, command_orchestrate)):
            arguments.work_item = work_cli_id(arguments.work_item, container=container)
        if getattr(arguments, "epic", None) is not None:
            arguments.epic = work_cli_id(arguments.epic, container=container)
        arguments.handler(arguments, container=container)
    except FleetError as error:
        error_console.print(f"fleet: {error}", style="red", markup=False,
                            soft_wrap=isinstance(error.__cause__, ItemResolved))
        sys.exit(2)
    except TimeoutExpired as error:
        error_console.print(f"fleet: {arguments.command} timed out after {error.timeout}s", style="red", markup=False)
        sys.exit(2)
    except KeyboardInterrupt:
        sys.exit(130)


if __name__ == "__main__":
    main()
