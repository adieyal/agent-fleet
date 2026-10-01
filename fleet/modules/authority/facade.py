from .application import Commands
from .domain import Activation


class AuthorityFacade:
    def __init__(self, repository, records, work, decisions=None, attention=None, execution=None):
        self.repository, self.work = repository, work
        self.decisions, self.attention, self.execution = decisions, attention, execution
        self.commands = Commands(repository, records, work)

    def activate(self, work_item: str | None = None, **fields) -> Activation:
        return self.commands.activate(work_item, **fields)

    def get(self, identity: str):
        return self.repository.get(identity)

    def require(self, command: str, work_item: str | None, **context):
        return self.commands.require(command, work_item, **context)

    def triage_mandate(self, activation: str):
        return self.commands.triage_mandate(activation)

    def require_triage(self, command: str, item, **context) -> Activation:
        return self.commands.require_triage(command, item, **context)

    def raise_attention(self, *, actor: str, activation: str, headline: str, context_reference: str):
        context = self.get(activation)
        self.require('raise_attention', context.work_item, actor=actor, activation=activation)
        return self.attention.raise_item(project=context.project, work_item=context.work_item,
            kind='decision', owner='user', source='authority', source_reference=activation + ':' + context_reference,
            headline=headline, context_reference=context_reference, actor=actor)

    def propose(self, *, actor: str, activation: str, question: str, change: str, reason: str):
        context = self.get(activation)
        self.require('propose', context.work_item, actor=actor, activation=activation)
        return self.decisions.propose(context, question=question, change=change, reason=reason)
