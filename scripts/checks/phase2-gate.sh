#!/usr/bin/env bash
# Run on carbon with current fleetd installed on carbon (local) and home (SSH).
# Usage: scripts/checks/phase2-gate.sh <epic-id> <carbon-cwd> <home-cwd>
# Uses the configured controller store. Creates two held jobs, no agent processes.
# Keep the printed run/job IDs for inspection with fleet status --json.
# Expected: one claim under contention; two actions for the epic on two hosts;
# a dropped SSH create reply recovers the same job; disconnect changes no work
# or run to complete/failed. Claims remain held until explicitly resolved.
set -euo pipefail
cd "$(dirname "$0")/../.."
uv run --frozen python - "$@" <<'PY'
import json
import select
import subprocess
import sys
from uuid import uuid4

from fleet import composition, transport
from fleet.errors import FleetError

if len(sys.argv) != 4:
    raise SystemExit("Supply an existing epic ID, carbon working directory and home working directory.")
item_id, carbon_cwd, home_cwd = sys.argv[1:]
hosts = [transport.host_by_name(name) for name in ("carbon", "home")]
assert hosts[0].is_local and not hosts[1].is_local, "Run on carbon; home must use SSH"
store = composition.open_store()
work = composition.open_work(store)
item = work.get(item_id)
assert item.kind == "epic", "Supply an epic work item"
before = (work.get(item_id), work.criteria(item_id), work.progress(item_id))
execution = composition.open_execution(store)
key = "phase2-" + str(uuid4())

def payload(cwd):
    return {"cwd": cwd, "arguments": ["create", "--project", item.project,
        "--description", key, "--agent", "codex", "--cwd", cwd,
        "--permission", "read-only", "--steps-file", "/dev/stdin", "--hold"],
        "steps": ["Reply with FLEET_STATUS: done. Do not use tools."], "context": [], "hold": True}

print("1. Two controller processes contend for one action; expect identical run IDs and four history rows.", flush=True)
program = '''
import json, sys
from fleet import composition
execution = composition.open_execution()
print("ready", flush=True)
sys.stdin.readline()
for _ in range(24):
    result = execution.dispatch(sys.argv[1], host="carbon", runtime="codex",
        payload=json.loads(sys.argv[2]), actor="user", reason="phase2 check", idempotency_key=sys.argv[3])
print(result.run.id, flush=True)
'''
sequence = store.latest_sequence()
processes = [subprocess.Popen([sys.executable, "-c", program, item_id, json.dumps(payload(carbon_cwd)), key],
    stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True) for _ in range(2)]
try:
    for process in processes:
        assert select.select([process.stdout], [], [], 10)[0], "claimant did not become ready"
        assert process.stdout.readline().strip() == "ready"
    for process in processes:
        process.stdin.write("go\n")
        process.stdin.flush()
    ids = []
    for process in processes:
        output, error = process.communicate(timeout=10)
        assert process.returncode == 0, error
        ids.append(output.strip())
    assert ids[0] == ids[1]
finally:
    for process in processes:
        if process.poll() is None:
            process.kill()
        process.wait(timeout=10)
run = execution.get_run(ids[0])
subjects = {f"execution:{kind}:{identity}" for kind, identity in
            (("action", run.action), ("run", run.id), ("claim", run.id), ("request", key))}
assert len([row for row in store.history_after(sequence) if row["subject"] in subjects]) == 4

print("2. Dispatch another action for the same epic to home; expect distinct actions and two held jobs.", flush=True)
other = execution.dispatch(item_id, host="home", runtime="codex", payload=payload(home_cwd),
    actor="user", reason="phase2 check", idempotency_key=key + "-home").run
assert other.action != run.action
execution.deliver(run, lambda args, stdin: transport.call(hosts[0], args, stdin_text=stdin), lambda *args: None)

print("3. Discard home's successful SSH create reply; expect reconcile to find the same run, without another create.", flush=True)
calls = []
def dropped(args, stdin):
    calls.append(args[0])
    reply = transport.call(hosts[1], args, stdin_text=stdin)
    if args[0] == "create":
        raise FleetError("injected dropped SSH reply after remote create")
    return reply
job = execution.deliver(other, dropped, lambda *args: None)
assert calls == ["create", "reconcile"] and job["run_id"] == other.id
for host, intended in zip(hosts, (run, other)):
    jobs = transport.call(host, ["ls", "--all"])["jobs"]
    matches = [job for job in jobs if job.get("run_id") == intended.id]
    assert len(matches) == 1 and matches[0]["id"] == intended.remote_job_id
    print(f"  {host.name}:{intended.remote_job_id} run={intended.id}")

print("4. Inject a disconnect during reconciliation; expect unknown outcome, active claims, unchanged work.", flush=True)
def disconnected(args, stdin):
    raise FleetError("injected SSH disconnect")
try:
    execution.deliver(other, disconnected, lambda *args: None, reconcile=True)
except FleetError:
    execution.unavailable("home")
else:
    raise AssertionError("disconnect was not surfaced")
sequence = store.latest_sequence()
execution.unavailable("home")
assert store.latest_sequence() == sequence
for intended in (run, other):
    assert execution.get_run(intended.id).status == "unknown outcome"
    claim, = [claim for claim in execution.claims() if claim.run == intended.id]
    assert claim.active
assert (work.get(item_id), work.criteria(item_id), work.progress(item_id)) == before
print("PASS. Two held jobs remain for inspection. Do not start them; resolve their unknown runs explicitly when finished.")
PY
