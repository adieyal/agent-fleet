"""Run the workspace guide with temporary state and a fake worker."""
import json
import re
import shlex
import subprocess
from pathlib import Path

from fleet import cli, composition


README = Path(__file__).resolve().parents[1] / 'README.md'


def test_workspace_guide(tmp_path, monkeypatch, capsys):
    text = README.read_text()
    assert '## Getting started with the workspace' in text
    section = text.split('## Getting started with the workspace\n', 1)[1].split('\n## ', 1)[0]
    constitution = tmp_path / 'constitution.md'
    constitution.write_text('# Constitution\n\nDecide yourself: test-only fixes.\n')
    values = {'MANAGEMENT_PATH': str(tmp_path / 'management'), 'WORKING_DIRECTORY': str(tmp_path),
              'JOB_ID': 'existing-job', 'CONSTITUTION_PATH': str(constitution)}
    calls = []

    def call(host, arguments, **fields):
        assert arguments[0] in ('create', 'start')
        calls.append(arguments)
        run = composition.open_execution().runs()[-1]
        return dict(id=run.remote_job_id, run_id=run.id, schema_version=4,
                    fingerprint=arguments[arguments.index('--fingerprint') + 1],
                    start_requested=arguments[0] == 'start',
                    status='running' if arguments[0] == 'start' else 'queued',
                    steps=[{}], description='Review')

    pushed = []
    monkeypatch.setattr(cli.transport, 'call', call)
    monkeypatch.setattr(cli, 'push_context', lambda host, job, paths: pushed.append(sorted(Path(p).name for p in paths)))
    for language, block in re.findall(r'```(bash|python)\n(.*?)```', section, re.S):
        for name, value in values.items():
            block = block.replace(name, value)
        if language == 'python':
            exec(compile(block, 'README mandate', 'exec'), {})
            records = composition.open_records()
            assert records.mandate_version(values['PROJECT_ID'], 'mandate.json')[0]
            continue
        for line in block.replace('\\\n', '').splitlines():
            args = shlex.split(line, comments=True)
            if not args:
                continue
            # IDs printed by earlier commands are filled in as the guide instructs.
            args = [values.get(arg, arg) for arg in args]
            if args[0] == 'git':
                subprocess.run(args, check=True, capture_output=True, timeout=10)
                continue
            assert args[0] == 'fleet'
            cli.main(args[1:])
            output = capsys.readouterr().out
            if args[1:3] == ['project', 'add']:
                name = 'DUPLICATE_ID' if 'PROJECT_ID' in values else 'PROJECT_ID'
                values[name] = re.search(r'p-[a-z0-9]+', output)[0]
            elif args[1:3] == ['work', 'add']:
                values['WORK_ID'] = json.loads(output)['id']
            elif args[1:3] == ['criterion', 'add']:
                values['CRITERION_ID'] = json.loads(output)['id']
            elif args[1:3] == ['attention', 'add']:
                values['ATTENTION_ID'] = json.loads(output)['id']
            elif args[1:3] == ['run', 'link']:
                values['RUN_ID'] = json.loads(output)['id']
    assert len(calls) == 6  # dispatch, send and orchestrate each create then start
    assert pushed == [['CONSTITUTION.md']] * 3  # the guide's constitution reaches each job
    for create in calls[::2]:
        assert create[create.index('--permission') + 1] == 'workspace-write'
    assert composition.open_work().summary(values['WORK_ID']).purpose == 'Ship the guide'


def test_readme_describes_store_and_upgrade():
    text = README.read_text()
    for stale in ('Projects are stored under', 'derived only from what the hosts report',
                  'kept in `workspace.json`', 'change `workspace.json`'):
        assert stale not in text
    for required in ('FLEET_STORE', 'fleet.db', 'backups', 'FLEET_FLEETD_PATH', 'FLEET_REMOTE_HOME',
                     'wire protocol', 'every host', 'fleet install'):
        assert required in text
