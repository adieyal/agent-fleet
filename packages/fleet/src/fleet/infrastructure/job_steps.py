"""Confirmed fleetd additions, including the explicit legacy unlinked-job retry path."""

import json

from fleet import transport
from fleet.modules.execution import StepRequest


def send_step(request: StepRequest) -> str:
    arguments = ['add', request.job, '--steps-file', '/dev/stdin']
    if request.retry:
        # fleetd's legacy --retry cannot be keyed. The controller permits one send per item.
        result = transport.call(transport.host_by_name(request.host), arguments + ['--retry'], stdin_text='[]')
        if not isinstance(result, dict) or result.get('id') != request.job or 'status' not in result:
            raise transport.FleetError('worker did not confirm the legacy retry; do not resend blindly')
        return f'retry submitted for job {request.job}; worker status {result["status"]}'
    arguments += ['--schema-version', '1', '--key', request.key]
    if request.answers is not None:
        arguments += ['--answers', str(request.answers)]
    result = transport.call(transport.host_by_name(request.host), arguments,
                            stdin_text=json.dumps([dict(prompt=request.prompt, title=request.title)]))
    if not isinstance(result, dict) or (result.get('schema_version'), result.get('key'), result.get('status'),
                                       result.get('answers')) != (1, request.key, 'applied', request.answers) or not (
            isinstance(result.get('steps'), list) and len(result['steps']) == 1
            and type(result['steps'][0]) is int):
        raise transport.FleetError('worker did not confirm the step addition')
    return f'added step {result["steps"][0] + 1} to job {request.job}'
