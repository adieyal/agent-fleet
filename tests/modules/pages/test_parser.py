import pytest

from fleet.modules.pages import PagesFacade, PageInvalid

pages = PagesFacade()


def test_three_directives_preserve_surrounding_prose():
    nodes = pages.parse('# Plan\n\n::work{id=w block=plan}\n\n::attention{id=a}\n\n::runs{project=p since=7d}\n\nEnd.')
    assert [node.kind for node in nodes] == ['prose', 'work', 'prose', 'attention', 'prose', 'runs', 'prose']
    assert nodes[1].block == 'plan'
    assert nodes[1].attributes['id'] == 'w'
    assert nodes[-1].text.endswith('End.')
    assert pages.title(nodes, 'plan') == 'Plan'


@pytest.mark.parametrize('body,reason', [
    ('::script{id=x}', 'Unknown directive: script'),
    ('::work{id=x extra=y}', 'Unknown attribute: extra'),
    ('::work{id=x id=y}', 'Duplicate attribute: id'),
    ('::work{}', 'Missing attribute: id'),
    ('::runs{project=p since=forever}', 'Invalid since: forever'),
    ('::runs{project=p since=0d}', 'Invalid since: 0d'),
    ('::runs{project=p since=366d}', 'Invalid since: 366d'),
    ('::work{id=x block=../escape}', 'Invalid block ID: ../escape'),
    ('::work{id=x}\nProse', 'standalone block'),
    ('::work{id=x', 'Invalid directive'),
])
def test_invalid_directives_are_named_errors(body, reason):
    assert reason in pages.parse(body)[0].error


def test_duplicate_block_is_an_error_and_fences_are_literal():
    nodes = pages.parse('```md\n::work{id=no}\n# Example\n```\n\n::work{id=x block=b}\n\n::attention{id=a block=b}')
    assert nodes[0].kind == 'prose'
    assert '::work{id=no}' in nodes[0].text
    assert nodes[-1].error == 'Duplicate block ID: b'
    assert pages.title(nodes, 'untitled') == 'untitled'


@pytest.mark.parametrize('slug', ['../x', 'Upper', 'x/y', '', 'x.md', '%2e%2e'])
def test_page_paths_are_confined(slug):
    with pytest.raises(PageInvalid, match='Invalid page slug'):
        pages.path(slug)


def test_size_and_directive_limits():
    with pytest.raises(PageInvalid, match='256 KiB'):
        pages.parse('é' * (128 * 1024 + 1))
    with pytest.raises(PageInvalid, match='100 directives'):
        pages.parse('\n\n'.join('::work{id=w}' for _ in range(101)))
