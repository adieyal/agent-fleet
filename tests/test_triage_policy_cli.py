import json
import pytest
from fleet import cli, composition
from tests.integration.test_triage_commands import triage


def test_policy_show_set_versions_and_rejects_invalid(triage, tmp_path, capsys):
    services, activation, _, _, body, _ = triage
    project = activation.project
    cli.main(['triage', 'policy', 'show', project])
    assert 'version 1' in capsys.readouterr().out
    body['limits']['runs_per_day'] = 7
    source = tmp_path / 'policy.json'
    source.write_text(json.dumps(body))
    cli.main(['triage', 'policy', 'set', project, '--file', str(source), '--actor', 'policy-author'])
    output = capsys.readouterr().out
    assert 'version 2 by policy-author' in output and 'limits:' in output and '7' in output
    assert 'active runs keep their pinned version' in output
    assert services.records.triage_policy(project)['policy']['limits']['runs_per_day'] == 7
    assert services.authority.triage_mandate(activation.id).limits['runs_per_day'] == 12
    cli.main(['triage', 'policy', 'set', project, '--file', str(source), '--actor', 'policy-author'])
    assert 'unchanged, version 2' in capsys.readouterr().out
    body['criteria_it_may_judge'] = ['criterion']
    source.write_text(json.dumps(body))
    with pytest.raises(SystemExit):
        cli.main(['triage', 'policy', 'set', project, '--file', str(source), '--actor', 'policy-author'])
    assert 'may not judge' in capsys.readouterr().err
    assert services.records.triage_policy(project)['version']['number'] == 2
