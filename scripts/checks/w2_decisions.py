"""Foreground, isolated controller-to-local-worker decision delivery check."""

import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[2]


def run_check(directory: Path) -> dict:
    directory.mkdir(parents=True, exist_ok=True)
    if list(directory.iterdir()):
        raise ValueError('the check requires an empty temporary directory')
    worker = directory / 'worker'
    worker.mkdir()
    inherited = {key: os.environ[key] for key in ('PATH', 'HOME', 'LANG', 'LC_ALL') if key in os.environ}
    environment = dict(inherited, FLEET_STORE=str(directory / 'store.db'),
        FLEET_CONFIG=str(directory / 'config.json'), FLEET_MANAGEMENT=str(directory / 'management'),
        FLEET_HOME=str(worker), FLEET_REMOTE_HOME=str(worker),
        FLEET_FLEETD_PATH=str(ROOT / 'fleet/remote/fleetd.py'), PYTHONPATH=str(ROOT),
        CLAUDE_CONFIG_DIR=str(directory / 'claude-config'))
    environment.pop('FLEET_JOB_ID', None)
    environment.pop('FLEET_JOB_DIR', None)
    (directory / 'config.json').write_text(json.dumps({'hosts': {'local': {'python': sys.executable}}}))
    agent = directory / 'scripted-agent'
    agent.write_text(f'''#!{sys.executable}
import json, pathlib, sys, time
prompt = sys.argv[sys.argv.index('-p') + 1]
first = 'W2 first step' in prompt
pathlib.Path('agent-step-1.md' if first else 'agent-step-2.md').write_text(prompt)
if first:
    pathlib.Path('step-1-started').write_text('running')
    time.sleep(3)
print(json.dumps(dict(type='result', subtype='success', result='Verified. FLEET_STATUS: done')))
''')
    agent.chmod(0o755)
    (worker / 'config.json').write_text(json.dumps({'claude': str(agent)}))

    def command(*arguments: str) -> str:
        return subprocess.run([sys.executable, '-m', 'fleet.cli', *arguments], cwd=ROOT,
            env=environment, check=True, capture_output=True, text=True, timeout=30).stdout

    seed = subprocess.run([sys.executable, '-c', '''
import json
from fleet.container import configured_container
s = configured_container().services()
configured_container(s.store).initialized_workspace()
p = s.workspace.edit_registry(lambda r: r.create('W2 isolated check'))
s.workspace.edit_registry(lambda r: r.link(p.id, 'local', 'w2-check'))
w = s.work.add(project=p.id, title='Decision delivery', goal='Receive mid-job decision', actor='check')
print(json.dumps(dict(project=p.id, work_item=w.id)))
'''], cwd=ROOT, env=environment, check=True, capture_output=True, text=True, timeout=30)
    identities = json.loads(seed.stdout)
    sent = json.loads(command('send', '--host', 'local', '--project', identities['project'],
        '--work-item', identities['work_item'], '--description', 'W2 two-step check', '--agent', 'claude',
        '--cwd', str(directory), '--step', 'W2 first step: sleep briefly', '--step', 'W2 second step: inspect decisions',
        '--hold', '--json'))
    job_id = sent['job'].split(':', 1)[1]
    job_file = worker / 'jobs' / job_id / 'job.json'
    # Own the runner as a child of this foreground check: no tmux or detached process.
    with (directory / 'runner.log').open('w') as log:
        runner = subprocess.Popen([sys.executable, str(ROOT / 'fleet/remote/fleetd.py'), '_run', job_id],
            cwd=ROOT, env=environment, stdout=log, stderr=subprocess.STDOUT)
        try:
            deadline = time.monotonic() + 30
            while not (directory / 'step-1-started').exists():
                if runner.poll() is not None:
                    raise RuntimeError('runner exited before first-step marker')
                if time.monotonic() >= deadline:
                    raise TimeoutError('first step did not start')
                time.sleep(.02)
            before = json.loads(job_file.read_text())
            assert before['steps'][0]['status'] == 'running'
            assert before['steps'][1]['status'] == 'pending'
            decision = json.loads(command('decision', 'record', '--work-item', identities['work_item'],
                '--question', 'Which colour?', '--answer', 'Use blue', '--actor', 'w2-check',
                '--principle', 'W2: decisions reach the next step'))
            during = json.loads(job_file.read_text())
            assert during['steps'][0]['status'] == 'running', 'record must complete during step 1'
            assert during['steps'][1]['status'] == 'pending'
            assert [d['id'] for d in during['decisions_since_dispatch']] == [decision['id']]
            runner.wait(timeout=30)
            assert runner.returncode == 0
        finally:
            if runner.poll() is None:
                runner.terminate()
                runner.wait(timeout=10)
    after = json.loads(job_file.read_text())
    prompt = (directory / 'agent-step-2.md').read_text()
    expected = ('Decisions recorded since this job started', 'Question: Which colour?',
                'Answer: Use blue', 'Actor: w2-check', 'Principle: W2: decisions reach the next step')
    assert all(text in prompt for text in expected)
    assert prompt.count('Question: Which colour?') == 1
    assert 'Which colour?' not in (directory / 'agent-step-1.md').read_text()
    assert [step['status'] for step in after['steps']] == ['done', 'done']
    assert after['steps'][1]['shown_decisions'] == [decision['id']]
    assert after['shown_decisions'] == [decision['id']]
    brief = worker / 'jobs' / job_id / 'brief-1.md'
    assert brief.read_text() == prompt
    evidence = dict(**identities, job_id=job_id, decision_id=decision['id'],
        recorded_during_step_1=True, step_statuses=[s['status'] for s in after['steps']],
        shown_decisions=after['shown_decisions'], step_2_prompt=prompt, temporary_directory=str(directory))
    (directory / 'evidence.json').write_text(json.dumps(evidence, indent=2) + '\n')
    return evidence


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True, help='directory for collected evidence')
    arguments = parser.parse_args()
    directory = Path(tempfile.mkdtemp(prefix='w2-decisions-', dir=os.environ["TMPDIR"]))
    evidence = run_check(directory)
    arguments.output.mkdir(parents=True, exist_ok=True)
    for name in ('evidence.json', 'agent-step-1.md', 'agent-step-2.md', 'runner.log'):
        shutil.copyfile(directory / name, arguments.output / ('e2e-' + name))
    print(json.dumps(evidence, indent=2))


if __name__ == '__main__':
    main()
