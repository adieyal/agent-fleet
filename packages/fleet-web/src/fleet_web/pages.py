"""Trusted HTML templates for library-resolved page values."""
from html import escape

from fleet_web.documents import render_markdown

STYLE = '''
:root{color-scheme:dark}*{box-sizing:border-box}body{margin:0;background:#131b20;color:#e2e8e5;font:17px/1.6 system-ui,sans-serif}
main{max-width:920px;margin:48px auto;padding:0 28px}a{color:#a8decc}nav{display:flex;gap:24px;font-size:14px;color:#9caeab}
h1{font-size:38px;letter-spacing:-1px;line-height:1.2}h2{font-size:23px}h3{margin:0;font-size:21px}header{border-bottom:1px solid #34423f;padding-bottom:24px;margin-bottom:30px}
.meta,.label{font-size:13px;color:#a7b5af}.label{text-transform:uppercase;letter-spacing:2px;color:#d8b778}.record{background:#1c292d;border-left:3px solid #d8b778;padding:20px 24px;margin:26px 0;border-radius:3px}
.error{border-color:#eea091;background:#35282a;color:#ffd5c9}.record p{margin:8px 0}dl{display:grid;grid-template-columns:105px 1fr;gap:8px 20px;margin:16px 0}dt{color:#a7b5af;font-size:14px}dd{margin:0}
ul{padding-left:22px}.runs{list-style:none;padding:0}.runs li{padding:12px 0;border-bottom:1px solid #34423f}code{overflow-wrap:anywhere;background:#172126;font-size:13px}pre{overflow:auto;padding:18px;background:#0f161a}
@media(max-width:600px){main{margin:24px auto;padding:0 18px}h1{font-size:30px}dl{grid-template-columns:1fr;gap:2px}dd{margin-bottom:8px}.record{padding:16px}}
'''


def text(value):
    return escape(str(value))


def shell(title, body):
    return f'<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>{text(title)} · Fleet</title><style>{STYLE}</style><main>{body}</main></html>'


def page_index(view):
    entries = ''.join(f'<li><a href="{text(page["url"])}">{text(page["title"])}</a></li>' for page in view['pages'])
    body = f'<nav><a href="/">Fleet</a></nav><header><p class="label">Project pages</p><h1>Pages</h1><p class="meta">{text(view["project"])}</p></header>'
    body += f'<ul>{entries}</ul>' if entries else f'<p>{text(view["empty_reason"])}</p>'
    return shell('Project pages', body)


def page_document(view):
    body = f'<nav><a href="/">Fleet</a><a href="/pages/{text(view["project"])}">Project pages</a></nav>'
    body += f'<header><p class="label">Live page · current records</p><p class="meta">{text(view["address"])}</p><p class="meta">Page revision <code>{text(view["revision"])}</code></p>'
    if view['historical']:
        body += '<p>Historical prose · directive values show current records.</p>'
    body += '</header>'
    for node in view['nodes']:
        if node['kind'] == 'prose':
            body += render_markdown(node['text'])['html']
            continue
        block = f' id="{text(node["block"])}"' if node['block'] and node['kind'] != 'error' else ''
        if node['kind'] == 'error':
            body += f'<section class="record error" role="alert"><strong>{text(node["error"])}</strong><p><code>{text(node["text"])}</code></p></section>'
            continue
        body += f'<section class="record"{block}><p class="label">{text(node["kind"])}</p>'
        if node['kind'] == 'work':
            record = node['record']
            progress = record['progress']
            body += f'<h3>{text(record["title"])}</h3><p>{text(record["goal"])}</p><dl>'
            value = 'Progress unknown: no defined total' if progress['total'] is None else f'{progress["complete"]} / {progress["total"]} {progress["basis"]}'
            body += f'<dt>Accepted</dt><dd>{text(value)}</dd><dt>Next step</dt><dd>{text(record["next_step"] or "No next step recorded")}</dd>'
            body += f'<dt>Condition</dt><dd>{text(record["condition"])}</dd>'
            if record['resume_condition']:
                body += f'<dt>Resumes when</dt><dd>{text(record["resume_condition"])}</dd>'
            body += '</dl>'
        elif node['kind'] == 'attention':
            record = node['record']
            body += f'<h3>{text(record["headline"])}</h3><p>{text(record["owner"])} · {text(record["state"])}</p><p>Source: {text(record["source"])}</p><p>Context: {text(record["context_reference"])}</p>'
            if record['resolution_details']:
                body += f'<p>Resolution: {text(record["resolution_details"])}</p>'
            else:
                body += '<p class="meta">Read view · answer this item in Fleet.</p>'
        elif node['kind'] == 'runs':
            if node['excluded_reason']:
                body += f'<p>{text(node["excluded_reason"])}</p>'
            if node['empty_reason']:
                body += f'<p>{text(node["empty_reason"])}</p>'
            else:
                body += '<ul class="runs">'
                for run in node['records']:
                    availability = {True: 'reachable', False: 'unavailable', None: 'reachability unknown'}[run['host_reachable']]
                    body += f'<li><strong>{text(run["title"] or run["remote_job_id"])}</strong> · {text(run["status"])}<br><span class="meta">{text(run["host"])} · {availability} · started {text(run["start"])}<br>Run {text(run["id"])}</span></li>'
                body += '</ul>'
        if not node['block']:
            body += '<p class="meta">Add a stable block ID to comment on this block.</p>'
        body += '</section>'
    body += f'<footer class="meta">Current records as of {text(view["snapshot_time"])} · state sequence {view["state_version"]}</footer>'
    return shell(view['title'], body)
