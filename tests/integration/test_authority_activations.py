import json
import subprocess

import pytest

from fleet.composition import open_authority, open_attention, open_decisions, open_records, open_store, open_work
from fleet.modules.authority import AuthorityRejected
from fleet.modules.work import EvidenceSpecification


@pytest.fixture
def context(tmp_path):
    root = tmp_path / 'management'
    root.mkdir()
    subprocess.run(['git', '-C', str(root), 'init'], check=True, capture_output=True, timeout=10)
    store = open_store()
    work = open_work(store)
    item = work.add(project='p', title='Task', goal='Ship', actor='user')
    judged = work.add_criterion(item.id, text='Review', verification='judged', actor='user')
    accepted = work.add_criterion(item.id, text='Accept', verification='accepted', actor='user')
    checked = work.add_criterion(item.id, text='Tests', verification='checked', actor='user',
                                 specification=EvidenceSpecification('missing-result'))
    records = open_records(store)
    records.register('p', root, actor='user')
    body = dict(goal='Ship', constraints=[], escalation_conditions=[],
                decision_authority=['update_progress', 'raise_attention', 'dispatch'],
                criteria_it_may_judge=[judged.id])
    revision = records.write_mandate('p', 'mandate.json', json.dumps(body), key='first', actor='user')['revision']
    authority = open_authority(store)
    activation = authority.activate(item.id, actor='agent', role='orchestrator', mandate_path='mandate.json')
    return store, work, item, judged, accepted, checked, records, body, revision, activation


def test_activation_binds_exact_commit_and_uses_it_after_mandate_changes(context):
    store, work, item, judged, _, _, records, body, revision, activation = context
    body['criteria_it_may_judge'] = []
    records.write_mandate('p', 'mandate.json', json.dumps(body), key='second', actor='user')
    authority = open_authority(store)
    assert authority.get(activation.id).mandate_version == revision
    assert (activation.actor, activation.role, activation.work_item) == ('agent', 'orchestrator', item.id)
    met = authority.meet(judged.id, actor='agent', activation=activation.id)
    assert (met.state, met.met_by, met.activation, met.mandate_version) == ('met', 'agent', activation.id, revision)
    assert open_work(store).criteria(item.id)[0] == met


@pytest.mark.parametrize('which,reason', [('accepted', 'user'), ('checked', 'evidence')])
def test_rejection_has_no_attention_or_history(context, which, reason):
    store, _, _, _, accepted, checked, _, _, _, activation = context
    criterion = accepted if which == 'accepted' else checked
    authority = open_authority(store)
    attention = open_attention(store)
    before = store.latest_sequence()
    with pytest.raises(AuthorityRejected, match=reason):
        authority.meet(criterion.id, actor='agent', activation=activation.id)
    assert store.latest_sequence() == before
    assert attention.list() == []


def test_proposal_is_explicit_and_records_one_attention_item(context):
    store, _, item, _, _, _, _, _, revision, activation = context
    proposal = open_authority(store).propose(actor='agent', activation=activation.id,
        question='May I accept?', change='Accept release', reason='Needs user review')
    assert open_decisions(store).proposals() == [proposal]
    assert (proposal.activation, proposal.mandate_version) == (activation.id, revision)
    attention, = open_attention(store).list()
    assert attention.work_item == item.id
    assert attention.context_reference == 'proposal:' + proposal.id


def test_identity_scope_and_ungranted_criterion_are_rejected(context):
    store, work, item, judged, _, _, _, _, _, activation = context
    authority = open_authority(store)
    with pytest.raises(AuthorityRejected, match='actor'):
        authority.meet(judged.id, actor='someone', activation=activation.id)
    other = work.add(project='p', title='Other', goal='Other', actor='user')
    with pytest.raises(AuthorityRejected, match='scope'):
        authority.update_progress(other.id, actor='agent', activation=activation.id, next_step='Go')
    extra = work.add_criterion(item.id, text='Extra', verification='judged', actor='user')
    with pytest.raises(AuthorityRejected, match='judge'):
        authority.meet(extra.id, actor='agent', activation=activation.id)
    updated = authority.update_progress(item.id, actor='agent', activation=activation.id, next_step='Review')
    assert updated.next_step == 'Review'
    assert updated.activation == activation.id


def test_ungranted_commands_and_malformed_proposals_write_nothing(context):
    store, _, item, _, _, _, records, body, _, _ = context
    body['decision_authority'] = []
    records.write_mandate('p', 'mandate.json', json.dumps(body), key='restricted', actor='user')
    authority = open_authority(store)
    activation = authority.activate(item.id, actor='agent', role='orchestrator', mandate_path='mandate.json')
    before = store.latest_sequence()
    with pytest.raises(AuthorityRejected, match='update_progress'):
        authority.update_progress(item.id, actor='agent', activation=activation.id, next_step='Go')
    with pytest.raises(AuthorityRejected, match='dispatch'):
        authority.dispatch(item.id, actor='agent', activation=activation.id)
    with pytest.raises(AuthorityRejected, match='raise_attention'):
        authority.raise_attention(actor='agent', activation=activation.id, headline='Question', context_reference='q')
    with pytest.raises(ValueError, match='reason'):
        authority.propose(actor='agent', activation=activation.id, question='Question', change='Change', reason='')
    assert store.latest_sequence() == before


def test_authorized_attention_and_dispatch_record_activation(context):
    from fleet.composition import open_execution, open_workspace

    store, _, item, _, _, _, _, _, revision, activation = context
    open_workspace(store)
    authority = open_authority(store)
    raised = authority.raise_attention(actor='agent', activation=activation.id, headline='Review', context_reference='review')
    assert raised.source_reference.startswith(activation.id + ':')
    result = authority.dispatch(item.id, actor='agent', activation=activation.id,
        host='local', runtime='codex', payload={'cwd': '/tmp'}, reason='Implement', idempotency_key='dispatch')
    action, = open_execution(store).actions()
    assert action.id == result.run.action
    assert (action.activation, action.mandate_version) == (activation.id, revision)


def test_work_public_command_checks_activation(context):
    store, work, _, _, accepted, checked, _, _, _, activation = context
    before = store.latest_sequence()
    for criterion in (accepted, checked):
        with pytest.raises(AuthorityRejected):
            work.meet(criterion.id, actor='agent', activation=activation.id)
    assert store.latest_sequence() == before
