"""Trusted HTML templates for library-resolved page values."""
from html import escape
import json

from markdown_it import MarkdownIt

prose_renderer = MarkdownIt('commonmark', {'html': False}).enable(['table', 'strikethrough'])

STYLE = '''
:root{color-scheme:dark}*{box-sizing:border-box}body{margin:0;background:#131b20;color:#e2e8e5;font:17px/1.6 system-ui,sans-serif}
main{max-width:920px;margin:48px auto;padding:0 28px}a{color:#a8decc}nav{display:flex;gap:24px;font-size:14px;color:#9caeab}
h1{font-size:38px;letter-spacing:-1px;line-height:1.2}h2{font-size:23px}h3{margin:0;font-size:21px}header{border-bottom:1px solid #34423f;padding-bottom:24px;margin-bottom:30px}
.meta,.label{font-size:13px;color:#a7b5af}.label{text-transform:uppercase;letter-spacing:2px;color:#d8b778}.record{background:#1c292d;border-left:3px solid #d8b778;padding:20px 24px;margin:26px 0;border-radius:3px}
.error{border-color:#eea091;background:#35282a;color:#ffd5c9}.record p{margin:8px 0}dl{display:grid;grid-template-columns:105px 1fr;gap:8px 20px;margin:16px 0}dt{color:#a7b5af;font-size:14px}dd{margin:0}
ul{padding-left:22px}.runs{list-style:none;padding:0}.runs li{padding:12px 0;border-bottom:1px solid #34423f}code{overflow-wrap:anywhere;background:#172126;font-size:13px}pre{overflow:auto;padding:18px;background:#0f161a}
@media(max-width:600px){main{margin:24px auto;padding:0 18px}h1{font-size:30px}dl{grid-template-columns:1fr;gap:2px}dd{margin-bottom:8px}.record{padding:16px}}
button,input,textarea,select{font:inherit}button{cursor:pointer;background:#b9ddcf;color:#14221e;border:0;border-radius:3px;padding:7px 13px}label{display:block;margin:12px 0}input,textarea,select{display:block;max-width:100%;width:100%;background:#131b20;color:#e2e8e5;border:1px solid #657a72;padding:8px}textarea{min-height:90px}.thread{border:1px solid #435852;padding:20px;margin:18px 0}.thread p{white-space:pre-wrap;overflow-wrap:anywhere}.thread .answer{border-left:3px solid #a8decc;padding-left:14px}.thread .detached{color:#ffd5c9}[hidden]{display:none!important}
.live-document{max-width:1240px}
.page-address{font-size:11px;overflow-wrap:anywhere}
header{padding-bottom:8px;margin-bottom:22px;border:0}
.page-layout{display:grid;grid-template-columns:minmax(0,760px) 290px;gap:40px;position:relative;align-items:start}
#page-content{min-width:0}
#page-margin{position:relative;font-size:13px}
#page-threads{position:relative}
.thread{position:absolute;left:0;right:0;margin:0;padding:12px 14px;border:0;border-radius:8px;background:#1b272b;box-shadow:0 1px 4px #0003;font-size:13px;line-height:1.5}
.thread p{margin:6px 0}
.thread .answer{border-left:2px solid #607e73;padding-left:10px;margin-top:12px}
.thread.active{box-shadow:0 0 0 1px #a8decc}
.thread-top{display:flex;align-items:center;gap:8px}
.thread-top time{color:#a7b5af;font-size:11px}
.thread-top button{margin-left:auto}
.thread button{font-size:12px;padding:3px 6px;background:transparent;color:#a8decc}
.thread textarea{min-height:70px;height:70px;resize:vertical;font-size:13px;border:0;border-bottom:1px solid #435852;padding:5px;background:transparent}
.reply-actions{display:flex;gap:6px}
.thread.resolved .thread-body{display:none}
.thread.resolved.expanded .thread-body{display:block}
.thread.resolved{background:transparent;box-shadow:none;padding:4px 8px}
.thread.resolved.active{box-shadow:0 0 0 1px #a8decc}
.detached-heading{position:absolute;color:#ffd5c9;font-size:12px}
.record{position:relative}
.block-comment{position:absolute;right:8px;top:8px;opacity:0;background:transparent;color:#a8decc;padding:2px 8px}
.record:hover .block-comment,.record:focus-within .block-comment{opacity:1}
.block-comment:focus-visible{opacity:1}
#comment-composer{position:absolute;left:0;right:0;z-index:5;background:#223136;border-radius:8px;padding:12px;box-shadow:0 4px 20px #0005}
#comment-composer textarea{font-size:14px;border:0;min-height:90px;resize:vertical}
.composer-actions{display:flex;align-items:center;gap:6px;margin-top:8px}
.composer-actions button{font-size:12px;padding:5px 8px}
.owner-label{display:flex;align-items:center;gap:4px;margin:0 auto 0 0;font-size:12px}
.owner-label select{width:auto;padding:3px;font-size:12px}
.sr-only{position:absolute;width:1px;height:1px;overflow:hidden;clip-path:inset(50%)}
#selection-comment{position:fixed;z-index:10;border-radius:50%;box-shadow:0 2px 10px #0005}
button:focus-visible,a:focus-visible,textarea:focus-visible,select:focus-visible{outline:2px solid #a8decc;outline-offset:3px}
#page-connection:empty{display:none}
#page-live{display:inline-flex;align-items:center;gap:6px;font-size:11px;color:#a7b5af;white-space:nowrap}
#page-live i{width:8px;height:8px;border-radius:50%;background:#6b7a75}
#page-live[data-state=live] i{background:#5fbf8a}#page-live[data-state=degraded] i{background:#d8b778}
#page-live[data-state=offline] i{background:#e0605a}#page-live[data-state=offline]{color:#f0a19c}
.anchor-badge{position:absolute;right:-24px;font-size:11px;padding:1px 5px;border-radius:12px}
.anchor-active{outline:1px solid #a8decc;outline-offset:3px}
#page-snapshot{margin-top:28px;font-size:11px}
.thread .meta{font-size:11px}
.annotation-active{background:#a8decc55!important}
@media(min-width:900px){.anchor-badge,.detached-badge{display:none}
}
@media(max-width:899px){#page-threads{height:auto!important}
.page-layout{display:block}

.live-document{max-width:800px;padding:0 32px 0 18px}
#page-margin{display:none}
body.comments-open #page-margin{display:block;position:fixed;bottom:0;left:0;right:0;height:55vh;background:#1b272b;z-index:20;padding:42px 16px 16px;overflow:auto;box-shadow:0 -8px 30px #0005;border-radius:14px 14px 0 0}
body.comments-open #page-threads .thread{display:none;position:relative;top:auto!important;margin-bottom:10px}
body.comments-open #page-threads .thread.sheet-visible{display:block}
#comment-composer{position:relative;top:auto!important}
body.comments-open #close-threads{display:block!important;position:fixed;right:14px;bottom:calc(55vh - 34px);z-index:21;font-size:12px}
.block-comment{opacity:1}
.page-prose{position:relative}
}
@media(prefers-reduced-motion:reduce){*{scroll-behavior:auto!important;transition:none!important}
}

'''


def text(value):
    return escape(str(value))


def shell(title, body, interactive=False):
    assets = ('<link rel="stylesheet" href="/vendor/recogito/text-annotator.css">'
              '<script src="/vendor/recogito/text-annotator.umd.js" defer></script>'
              '<script src="/js/pages.js" defer></script>') if interactive else ''
    main_class = ' class="live-document"' if interactive else ''
    return f'<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>{text(title)} · Fleet</title><style>{STYLE}</style>{assets}<main{main_class}>{body}</main></html>'


def page_index(view):
    entries = ''.join(f'<li><a href="{text(page["url"])}">{text(page["title"])}</a></li>' for page in view['pages'])
    body = f'<nav><a href="/">Fleet</a></nav><header><p class="label">Project pages</p><h1>Pages</h1><p class="meta">{text(view["project"])}</p></header>'
    body += f'<ul>{entries}</ul>' if entries else f'<p>{text(view["empty_reason"])}</p>'
    return shell('Project pages', body)


def directive_html(node):
    body = ''
    block = f' id="{text(node["block"])}"' if node['block'] and node['kind'] != 'error' else ''
    if node['kind'] == 'error':
        body += f'<section class="record error" role="alert"><strong>{text(node["error"])}</strong><p><code>{text(node["text"])}</code></p></section>'
        return body
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
    if node['block']:
        body += f'<button type="button" class="block-comment" aria-label="Comment on block" title="Comment on block" data-comment-block="{text(node["block"])}"><svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" aria-hidden="true"><path d="M4 4h16v12H9l-5 4V4Z"/><path d="M8 8h8M8 12h5"/></svg></button>'
    if not node['block']:
        body += '<p class="meta">Add a stable block ID to comment on this block.</p>'
    body += '</section>'
    return body


def page_document(view):
    body = f'<nav><a href="/">Fleet</a><a href="/pages/{text(view["project"])}">Project pages</a></nav>'
    body += f'<header><p class="meta page-address">{text(view["address"])} · revision <code>{text(view["revision"])}</code>'
    body += ' · <span id="page-live" data-state="connecting"><i aria-hidden="true"></i><span>Connecting</span></span></p>'
    if view['historical']:
        body += '<p>Historical prose · directive values show current records.</p>'
    body += '</header>'
    body += '<p class="meta" id="page-connection" role="status"></p><div class="page-layout"><article id="page-content">'
    for index, node in enumerate(view['nodes']):
        if node['kind'] == 'prose':
            body += f'<div class="page-prose" data-prose-node="{index}">{prose_renderer.render(node["text"])}</div>'
        else:
            body += f'<div data-directive-node="{index}">{directive_html(node)}</div>'
    body += '</article>'
    body += '''<aside id="page-margin" aria-label="Page threads"><div id="page-threads"></div><section id="comment-composer" hidden aria-label="New comment"><p id="comment-anchor" class="meta"></p><form id="comment-form"><label class="sr-only" for="comment-body">Comment</label><textarea id="comment-body" name="body" required maxlength="8192" placeholder="Add a comment…"></textarea><div class="composer-actions"><label class="owner-label">To <select name="owner" aria-label="Who must respond?"><option value="user">me</option><option value="agent">agent</option></select></label><button type="submit">Comment</button><button type="button" id="cancel-comment">Cancel</button></div><p class="meta">Agent requests queue a bounded triage run under the confirmed project mandate.</p><p id="comment-error" role="alert"></p></form></section></aside></div><button id="selection-comment" type="button" hidden aria-label="Comment on selection" title="Comment on selection"><svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" aria-hidden="true"><path d="M4 4h16v12H9l-5 4V4Z"/><path d="M8 8h8M8 12h5"/></svg></button><button id="close-threads" type="button" hidden aria-label="Close comments">Close</button>'''
    payload = json.dumps(view, default=str).replace('<', r'\u003c').replace('>', r'\u003e').replace('&', r'\u0026')
    body += f'<script type="application/json" id="page-data">{payload}</script>'
    body += f'<footer class="meta" id="page-snapshot">Current records as of {text(view["snapshot_time"])} · state sequence {view["state_version"]}</footer>'
    return shell(view['title'], body, interactive=True)
