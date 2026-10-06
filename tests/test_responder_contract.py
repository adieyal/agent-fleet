"""Installed-binary contract: no model calls, no user configuration discovery."""
import ast
import hashlib
import inspect
import json
import os
import shutil
import subprocess
from pathlib import Path

from fleet.infrastructure.codex.app_server import CodexAppServer


def test_installed_codex_app_server_contract(tmp_path):
    binary = shutil.which('codex')
    assert binary, 'installed codex binary missing for responder contract test'
    environment = dict(os.environ, HOME=str(tmp_path), CODEX_HOME=str(tmp_path / 'codex'))
    version = subprocess.run([binary, '--version'], env=environment, check=True,
                             capture_output=True, text=True, timeout=15).stdout.strip()
    generated = tmp_path / 'schema'
    subprocess.run([binary, 'app-server', 'generate-json-schema', '--out', str(generated)],
                   env=environment, check=True, capture_output=True, text=True, timeout=30)
    requests = json.loads((generated / 'ClientRequest.json').read_text())
    notifications = json.loads((generated / 'ServerNotification.json').read_text())
    client_notifications = json.loads((generated / 'ClientNotification.json').read_text())
    used_requests = {'initialize': ('InitializeParams', ['clientInfo']),
                     'thread/start': ('ThreadStartParams', ['ephemeral', 'sandbox', 'approvalPolicy', 'cwd']),
                     'turn/start': ('TurnStartParams', ['threadId', 'effort', 'outputSchema', 'input']),
                     'turn/interrupt': ('TurnInterruptParams', ['threadId', 'turnId'])}
    used_notifications = {
        'item/agentMessage/delta': ('AgentMessageDeltaNotification', ['threadId', 'turnId', 'delta']),
        'item/completed': ('ItemCompletedNotification', ['threadId', 'turnId', 'item']),
        'thread/tokenUsage/updated': ('ThreadTokenUsageUpdatedNotification', ['threadId', 'turnId', 'tokenUsage']),
        'turn/completed': ('TurnCompletedNotification', ['threadId', 'turn']),
    }
    assert '"jsonrpc"' not in inspect.getsource(CodexAppServer), 'extra undocumented envelope field'
    checked = []
    def fields(document, name, names):
        value = document['definitions'][name]
        assert set(names) <= set(value['properties']), (name, names)
        checked.extend(f'{name}.{field}' for field in names)
        return value
    for document, used in [(requests, used_requests), (notifications, used_notifications)]:
        variants = {item['properties']['method']['enum'][0]: item for item in document['oneOf']}
        for method, (name, names) in used.items():
            assert method in variants, method
            assert variants[method]['properties']['params']['$ref'] == '#/definitions/' + name
            fields(document, name, names)
    assert any(item['properties']['method']['enum'] == ['initialized']
               for item in client_notifications['oneOf'])
    literals = {node.value for node in ast.walk(ast.parse(inspect.getsource(CodexAppServer)))
                if isinstance(node, ast.Constant) and isinstance(node.value, str) and '/' in node.value
                and ' ' not in node.value}
    assert literals == (set(used_requests) | set(used_notifications)) - {'initialize'}
    fields(requests, 'ClientInfo', ['name', 'version'])
    text = next(item for item in requests['definitions']['UserInput']['oneOf']
                if item['properties']['type']['enum'] == ['text'])
    assert {'type', 'text'} <= set(text['properties'])
    assert set(text['required']) <= {'type', 'text'}
    checked.extend(['UserInput.text.type=text', 'UserInput.text.text'])
    for name in ['ThreadStartResponse', 'TurnStartResponse']:
        document = json.loads((generated / 'v2' / (name + '.json')).read_text())
        field = 'thread' if name == 'ThreadStartResponse' else 'turn'
        assert field in document['properties']
        target = document['properties'][field]['$ref'].rsplit('/', 1)[1]
        fields(document, target, ['id'])
    fields(notifications, 'Turn', ['id', 'status', 'items', 'error'])
    fields(notifications, 'ThreadTokenUsage', ['last'])
    fields(notifications, 'TokenUsageBreakdown', ['inputTokens', 'outputTokens', 'cachedInputTokens',
                                               'totalTokens', 'reasoningOutputTokens', 'cacheWriteInputTokens'])
    message = next(item for item in notifications['definitions']['ThreadItem']['oneOf']
                   if item['properties']['type']['enum'] == ['agentMessage'])
    assert {'type', 'id', 'text'} <= set(message['properties'])
    checked.extend(['ThreadItem.agentMessage.type', 'ThreadItem.agentMessage.id', 'ThreadItem.agentMessage.text'])
    effort = requests['definitions']['ReasoningEffort']
    assert effort['type'] == 'string' and effort['minLength'] <= len('low')
    assert 'read-only' in requests['definitions']['SandboxMode']['enum']
    policy = requests['definitions']['AskForApproval']['oneOf']
    assert any('never' in item.get('enum', []) for item in policy)
    for name, keys in [('JSONRPCRequest', ['id', 'method', 'params']),
                       ('JSONRPCResponse', ['id', 'result']), ('JSONRPCError', ['id', 'error']),
                       ('JSONRPCNotification', ['method', 'params'])]:
        document = json.loads((generated / (name + '.json')).read_text())
        assert set(keys) <= set(document['properties'])
        checked.extend(f'{name}.{key}' for key in keys)
    error = json.loads((generated / 'JSONRPCError.json').read_text())
    fields(error, 'JSONRPCErrorError', ['code', 'message'])
    assert any(item.get('type') == 'integer' for item in requests['definitions']['RequestId']['anyOf'])
    report = {'codex_version': version, 'methods': sorted(set(used_requests) | set(used_notifications) | {'initialized'}),
              'fields': checked, 'schema_files': len(list(generated.rglob('*.json'))),
              'bundle_sha256': hashlib.sha256((generated / 'codex_app_server_protocol.v2.schemas.json').read_bytes()).hexdigest()}
    print('CODEX_CONTRACT ' + json.dumps(report, sort_keys=True))
    destination = Path(os.environ.get('FLEET_RESPONDER_CONTRACT_REPORT', tmp_path / 'contract.json'))
    destination.write_text(json.dumps(report, indent=2) + '\n')
