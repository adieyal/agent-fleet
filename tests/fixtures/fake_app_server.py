"""Scripted stdio peer. No network, user credentials or Codex dependency."""
import json
import os
import signal
import subprocess
import sys
from pathlib import Path

if sys.argv[1:] == ['--version']:
    print('codex-cli fake-0.160.1')
    sys.exit(0)

mode = os.environ.get('FAKE_CODEX_MODE', 'normal')
# Provisioning deliberately strips arbitrary inherited variables. Test scripts
# set a mode in their wrapper instead.
if len(sys.argv) > 2:
    mode = sys.argv[2]
if mode == 'ignore-term':
    signal.signal(signal.SIGTERM, signal.SIG_IGN)
threads = 0
turns = 0
active = None


def send(value):
    print(json.dumps({'jsonrpc': '2.0', **value}), flush=True)


def event(method, params):
    send({'method': method, 'params': params})


for line in sys.stdin:
    message = json.loads(line)
    method, params = message.get('method'), message.get('params', {})
    rid = message.get('id')
    if method == 'initialized':
        continue
    if method == 'initialize':
        assert params['clientInfo']['name'] == 'fleet-responder'
        if mode == 'child':
            child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(120)'])
            (Path(os.environ['CODEX_HOME']) / 'child.pid').write_text(str(child.pid))
        if mode == 'init-death':
            sys.exit(23)
        if mode == 'init-hang':
            continue
        if mode == 'malformed':
            print('broken json', flush=True)
            continue
        send({'id': rid, 'result': {'userAgent': 'fake'}})
    elif method == 'thread/start':
        assert params['ephemeral'] and params['sandbox'] == 'read-only'
        assert params['approvalPolicy'] == 'never'
        assert params['cwd'] == os.environ['HOME']
        assert not (Path(os.environ['HOME']) / '.agents').exists()
        threads += 1
        send({'id': rid, 'result': {'thread': {'id': f'thread-{threads}'}}})
    elif method == 'turn/start':
        if mode == 'rpc-error':
            send({'id': rid, 'error': {'code': -32602, 'message': 'bad turn'}})
            continue
        turns += 1
        active = {'threadId': params['threadId'], 'turnId': f'turn-{turns}'}
        assert params['effort'] == 'low'
        assert params['outputSchema']['type'] == 'object'
        assert params['input'][0]['type'] == 'text'
        if mode == 'start-hang':
            continue
        send({'id': rid, 'result': {'turn': {'id': active['turnId']}}})
        if mode == 'turn-death':
            sys.exit(24)
        if mode == 'hang':
            continue
        if mode == 'server-request':
            send({'id': 'permission', 'method': 'item/permissions/requestApproval', 'params': active})
            continue
        # Old and unrelated events must not contaminate the current turn.
        event('item/agentMessage/delta', {**active, 'turnId': 'old', 'delta': 'WRONG'})
        event('item/agentMessage/delta', {**active, 'threadId': 'other', 'delta': 'WRONG'})
        body = {'reply': 'pong', 'escalate': False, 'reason': ''}
        if set(params['outputSchema']['properties']) == {'reply'}:
            body = {'reply': 'Inbox task is in Later.'}
        reply = json.dumps(body)
        for delta in [reply[:12], reply[12:]]:
            event('item/agentMessage/delta', {**active, 'itemId': 'answer', 'delta': delta})
        item = {'id': 'answer', 'type': 'agentMessage', 'text': reply}
        event('item/completed', {**active, 'item': item})
        event('thread/tokenUsage/updated', {**active, 'tokenUsage': {'last': {
            'inputTokens': 2200, 'cachedInputTokens': 0, 'outputTokens': 20,
            'reasoningOutputTokens': 0, 'totalTokens': 2220}}})
        status = 'failed' if mode == 'failed' else 'completed'
        event('turn/completed', {'threadId': active['threadId'], 'turn': {
            'id': active['turnId'], 'items': [item], 'status': status,
            'error': {'message': 'scripted model failure'} if status == 'failed' else None}})
    elif method == 'turn/interrupt':
        assert params == active
        send({'id': rid, 'result': {}})
        event('turn/completed', {'threadId': active['threadId'], 'turn': {
            'id': active['turnId'], 'items': [], 'status': 'interrupted', 'error': None}})
    else:
        send({'id': rid, 'error': {'code': -32601, 'message': 'unknown method'}})
