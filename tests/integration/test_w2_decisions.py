"""A real local fleetd supplies a mid-job decision to the second scripted agent."""

from scripts.checks.w2_decisions import run_check


def test_decision_reaches_next_agent_process(tmp_path):
    evidence = run_check(tmp_path / 'w2-check')
    assert evidence['recorded_during_step_1']
    assert evidence['step_statuses'] == ['done', 'done']
    assert evidence['shown_decisions'] == [evidence['decision_id']]
    assert 'Answer: Use blue' in evidence['step_2_prompt']
