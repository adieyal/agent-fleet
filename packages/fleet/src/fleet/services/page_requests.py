"""Identity of the pending user request in an agent-owned page thread."""


def request_token(item):
    messages = [reply.id for reply in item.replies if not reply.actor.startswith('triage:')]
    return str(item.owner_at) + ':' + (messages[-1] if messages else 'initial')
