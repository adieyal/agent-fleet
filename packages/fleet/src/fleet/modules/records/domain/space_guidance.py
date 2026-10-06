"""The structured space section of Fleet's project constitution.

The surrounding Markdown remains user authored. The fenced data is also read
by clients and is included verbatim in every dispatched constitution.
"""
import json
import re

SECTION = re.compile(r'<!-- fleet:space-guidance -->\n.*?\n<!-- /fleet:space-guidance -->', re.DOTALL)
DATA = re.compile(r'```fleet-space-guidance\n(.*?)\n```', re.DOTALL)


def space_guidance(body: str) -> dict | None:
    sections = SECTION.findall(body)
    if not sections:
        if '<!-- fleet:space-guidance -->' in body or '<!-- /fleet:space-guidance -->' in body:
            raise ValueError('space guidance section has unmatched markers')
        return None
    if len(sections) != 1:
        raise ValueError('constitution has multiple space guidance sections')
    match = DATA.search(sections[0])
    if match is None:
        raise ValueError('space guidance section is missing its structured data')
    value = json.loads(match[1])
    if (not isinstance(value, dict) or not isinstance(value.get('north_star'), str)
            or not isinstance(value.get('clauses'), list) or not isinstance(value.get('scope'), dict)):
        raise ValueError('space guidance requires north_star, clauses and scope')
    kinds = {'impl', 'deps', 'ops', 'destructive', 'interface', 'arch', 'scope', 'process'}
    if any(kind not in kinds or level not in ('decide', 'tell', 'ask') for kind, level in value['scope'].items()):
        raise ValueError('invalid decision scope in constitution')
    for clause in value['clauses']:
        if (not isinstance(clause, dict) or not isinstance(clause.get('text'), str)
                or clause.get('kind') not in ('guidance', 'enforced')):
            raise ValueError('invalid clause in constitution')
        if clause['kind'] == 'enforced' and (not isinstance(clause.get('rule'), str) or not clause['rule'].strip()):
            raise ValueError('an enforced clause must name its rule')
    return value


def with_space_guidance(body: str, value: dict) -> str:
    data = {key: value[key] for key in ('north_star', 'clauses', 'scope')}
    section = ('<!-- fleet:space-guidance -->\n## Space guidance\n\n'
               'The north star and clauses guide all work in this project. Decision scope maps each kind '
               'to decide, tell, or ask. Enforced clauses outrank that scope.\n\n'
               '```fleet-space-guidance\n' + json.dumps(data, indent=2, ensure_ascii=False) +
               '\n```\n<!-- /fleet:space-guidance -->')
    space_guidance(section)  # Validate before a version can be written.
    space_guidance(body)  # Refuse malformed or ambiguous existing sections.
    if SECTION.search(body):
        return SECTION.sub(lambda _: section, body)
    return body.rstrip() + '\n\n' + section + '\n'
