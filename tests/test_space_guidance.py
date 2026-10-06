"""Records validates shared guidance without replacing user-authored prose."""
import pytest

from fleet.modules.records.domain.space_guidance import space_guidance, with_space_guidance


def test_round_trip_preserves_text_around_section():
    value = {'north_star': 'Stable API', 'clauses': [], 'scope': {'impl': 'tell'}}
    body = with_space_guidance('# Rules\n\nKeep provenance.\n', value) + '\n## More\n\nKeep this too.\n'
    edited = with_space_guidance(body, value | {'north_star': 'Shared API'})
    assert edited.startswith('# Rules\n\nKeep provenance.\n')
    assert edited.endswith('\n## More\n\nKeep this too.\n')
    assert space_guidance(edited)['north_star'] == 'Shared API'


@pytest.mark.parametrize('value', [
    {'north_star': 'Ship', 'clauses': [], 'scope': {'unknown': 'decide'}},
    {'north_star': 'Ship', 'clauses': [], 'scope': {'impl': 'never'}},
    {'north_star': 'Ship', 'clauses': [], 'scope': {'impl': []}},
    {'north_star': 'Ship', 'clauses': [{'text': 'Ask', 'kind': 'enforced'}], 'scope': {}},
])
def test_invalid_structured_guidance_is_refused(value):
    with pytest.raises(ValueError):
        with_space_guidance('', value)


def test_missing_data_does_not_create_a_default_scope():
    assert space_guidance('# Constitution\n\nNo decision scope.\n') is None
    with pytest.raises(ValueError, match='unmatched'):
        space_guidance('<!-- fleet:space-guidance -->\nMissing closing marker')
