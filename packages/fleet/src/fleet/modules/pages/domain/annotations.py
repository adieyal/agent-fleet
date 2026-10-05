"""W3C anchors against canonical prose; no client-supplied DOM or HTML is trusted."""
import re
from html import unescape

import mistune

from . import PageInvalid

SPACE = re.compile(r'[ \t\r\n\f\v\u00a0]+')
BLOCKS = {'paragraph', 'heading', 'block_text', 'block_code', 'block_html', 'list_item', 'table_cell', 'table_row'}


def normalize(value):
    return SPACE.sub(' ', value).strip(' ')


def prose_text(markdown):
    tokens = mistune.create_markdown(renderer='ast', plugins=['table', 'strikethrough'])(markdown)

    def content(token):
        if token['type'] == 'image':
            return ''
        if token['type'] in ('softbreak', 'linebreak', 'blank_line'):
            return '\n'
        if 'children' in token:
            value = ''.join(content(child) for child in token['children'])
        else:
            value = token.get('raw', '')
            if token['type'] == 'text':
                value = unescape(value)
        return value + ('\n' if token['type'] in BLOCKS else '')

    return normalize(''.join(content(token) for token in tokens))


def prose_segments(nodes):
    parts = []
    offset = 0
    for index, node in enumerate(nodes):
        if node.kind == 'prose':
            value = prose_text(node.text)
            parts.append(dict(node=index, text=value, start=offset, end=offset + len(value)))
            offset += len(value) + 1
    return parts


def attachment(nodes, selector, original_nodes=None):
    selectors = selector if isinstance(selector, list) else [selector]
    if len(selectors) == 1 and selectors[0].get('type') == 'FragmentSelector':
        block = selectors[0]['value']
        current = next((node for node in nodes if node.block == block and node.kind not in ('prose', 'error')), None)
        exists = current is not None
        if current is not None and original_nodes is not None:
            original = next((node for node in original_nodes if node.block == block and node.kind not in ('prose', 'error')), None)
            if original is None or (current.kind, current.attributes.get('id'), current.attributes.get('project')) != (
                    original.kind, original.attributes.get('id'), original.attributes.get('project')):
                return dict(state='unavailable', block=block, reason=f'Anchor unavailable: block {block} changed target')
        return dict(state='attached' if exists else 'unavailable', block=block,
                    reason=None if exists else f'Anchor unavailable: block {block} removed')
    quote = next((entry for entry in selectors if entry.get('type') == 'TextQuoteSelector'), None)
    if quote is None:
        raise PageInvalid('TextQuoteSelector is required')
    parts = prose_segments(nodes)
    document = ' '.join(part['text'] for part in parts)
    exact, prefix, suffix = quote['exact'], quote.get('prefix', ''), quote.get('suffix', '')
    matches = []
    for match in re.finditer(f'(?={re.escape(exact)})', document):
        start, end = match.start(), match.start() + len(exact)
        if (document[:start].endswith(prefix) and document[end:].startswith(suffix)):
            segment = next((part for part in parts if part['start'] <= start and end <= part['end']), None)
            if segment:
                matches.append(dict(node=segment['node'], start=start, end=end,
                                    local_start=start - segment['start'], local_end=end - segment['start']))
    if len(matches) == 1:
        return dict(state='attached', reason=None, **matches[0])
    return dict(state='ambiguous' if matches else 'unavailable', reason=(
        'Anchor ambiguous: multiple matching passages' if matches else 'Anchor unavailable: quoted text changed'))


def validate_selector(nodes, selector):
    if not isinstance(selector, (dict, list)):
        raise PageInvalid('selector must be a W3C selector object or list')
    entries = selector if isinstance(selector, list) else [selector]
    if not entries or len(entries) > 2 or any(not isinstance(entry, dict) for entry in entries):
        raise PageInvalid('one quote and optional position, or one block selector, are required')
    types = [entry.get('type') for entry in entries]
    if len(set(types)) != len(types):
        raise PageInvalid('duplicate selector types')
    allowed = {'TextQuoteSelector': {'type', 'exact', 'prefix', 'suffix'},
               'TextPositionSelector': {'type', 'start', 'end'}, 'FragmentSelector': {'type', 'value'}}
    for entry in entries:
        kind = entry.get('type')
        if kind not in allowed or entry.keys() - allowed[kind]:
            raise PageInvalid(f'Unknown selector type or fields: {kind}')
        if kind == 'TextQuoteSelector':
            if not isinstance(entry.get('exact'), str) or not entry['exact'].strip():
                raise PageInvalid('quote exact is required')
            for field in ('exact', 'prefix', 'suffix'):
                value = entry.get(field, '')
                if not isinstance(value, str) or SPACE.sub(' ', value) != value:
                    raise PageInvalid(f'{field} must use canonical whitespace')
        elif kind == 'TextPositionSelector':
            if any(type(entry.get(field)) is not int for field in ('start', 'end')) or not 0 <= entry['start'] < entry['end']:
                raise PageInvalid('invalid text positions')
        elif not isinstance(entry.get('value'), str):
            raise PageInvalid('block ID is required')
    if 'FragmentSelector' in types and len(entries) != 1:
        raise PageInvalid('block selection cannot span prose')
    if 'TextPositionSelector' in types and 'TextQuoteSelector' not in types:
        raise PageInvalid('TextQuoteSelector is required')
    result = attachment(nodes, selector)
    if result['state'] != 'attached':
        raise PageInvalid(result['reason'])
    position = next((entry for entry in entries if entry['type'] == 'TextPositionSelector'), None)
    if position and (position['start'], position['end']) != (result['start'], result['end']):
        raise PageInvalid('text positions disagree with the quote')
    return result
