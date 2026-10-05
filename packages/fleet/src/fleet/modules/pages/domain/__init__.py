"""Strict, non-executable page syntax."""
import re
from dataclasses import dataclass, field

SLUG = re.compile(r'[a-z0-9]+(?:-[a-z0-9]+)*\Z')
DIRECTIVE = re.compile(r'::([a-z]+)\{([^{}]*)\}\s*\Z')
ATTRIBUTE = re.compile(r'([a-z]+)=([^\s=]+)')


class PageInvalid(ValueError):
    """An invalid page address or body."""


class PageNotFound(LookupError):
    """A page or confirmed revision is unavailable."""


@dataclass(frozen=True)
class PageNode:
    kind: str
    text: str = ''
    attributes: dict[str, str] = field(default_factory=dict)
    block: str | None = None
    error: str | None = None


def page_path(slug: str) -> str:
    if not SLUG.fullmatch(slug):
        raise PageInvalid(f'Invalid page slug: {slug}')
    return f'pages/{slug}.md'


def parse(markdown: str) -> list[PageNode]:
    if len(markdown.encode()) > 256 * 1024:
        raise PageInvalid('Page exceeds 256 KiB')
    nodes, prose, blocks = [], [], set()
    fence = None
    count = 0

    def flush():
        if prose:
            nodes.append(PageNode('prose', ''.join(prose)))
            prose.clear()

    lines = markdown.splitlines(keepends=True)
    for index, line in enumerate(lines):
        stripped = line.rstrip('\r\n')
        marker = re.match(r'^ {0,3}(`{3,}|~{3,})(.*)$', stripped)
        if fence:
            prose.append(line)
            if marker and marker[1][0] == fence[0] and len(marker[1]) >= fence[1] and not marker[2].strip():
                fence = None
            continue
        if marker:
            fence = (marker[1][0], len(marker[1]))
            prose.append(line)
            continue
        if not stripped.startswith('::'):
            prose.append(line)
            continue
        flush()
        count += 1
        if count > 100:
            raise PageInvalid('Page exceeds 100 directives')
        error, attrs, block = None, {}, None
        match = DIRECTIVE.fullmatch(stripped)
        kind = match[1] if match else 'invalid'
        if not match:
            error = f'Invalid directive: {stripped}'
        elif kind not in ('work', 'attention', 'runs'):
            error = f'Unknown directive: {kind}'
        else:
            for token in match[2].split():
                attribute = ATTRIBUTE.fullmatch(token)
                if not attribute:
                    error = f'Invalid attribute: {token}'
                    break
                key, value = attribute.groups()
                if key in attrs:
                    error = f'Duplicate attribute: {key}'
                    break
                attrs[key] = value
            required = {'project', 'since'} if kind == 'runs' else {'id'}
            extra = attrs.keys() - required - {'block'}
            missing = required - attrs.keys()
            if not error and extra:
                error = f'Unknown attribute: {sorted(extra)[0]}'
            if not error and missing:
                error = f'Missing attribute: {sorted(missing)[0]}'
            if not error and kind == 'runs' and not re.fullmatch(r'(?:[1-9]|[1-9][0-9]|[12][0-9]{2}|3[0-5][0-9]|36[0-5])d', attrs['since']):
                error = f'Invalid since: {attrs["since"]}'
            block = attrs.get('block')
            if block and not SLUG.fullmatch(block):
                error = f'Invalid block ID: {block}'
            elif block in blocks:
                error = f'Duplicate block ID: {block}'
            elif block:
                blocks.add(block)
        if not error and ((index and lines[index - 1].strip()) or
                          (index + 1 < len(lines) and lines[index + 1].strip())):
            error = f'Directive must occupy a standalone block: {stripped}'
        nodes.append(PageNode('error' if error else kind, stripped, attrs, block, error))
    flush()
    return nodes


def title(nodes: list[PageNode], slug: str) -> str:
    for node in nodes:
        if node.kind == 'prose':
            # Fenced examples are not titles.
            fence = False
            for line in node.text.splitlines():
                if re.match(r'^ {0,3}(?:`{3,}|~{3,})', line):
                    fence = not fence
                elif not fence and (heading := re.match(r'^ {0,3}#{1,6}\s+(.+?)\s*#*$', line)):
                    return heading[1]
    return slug
