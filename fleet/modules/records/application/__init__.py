"""Intent-first authoring and recovery."""

import hashlib
from uuid import uuid4


class Authoring:
    def __init__(self, repository, writer, workspace):
        self.repository, self.writer, self.workspace = repository, writer, workspace

    def prepare(self, project: str, path: str, body: str, *, key: str, actor: str,
                source_run: str | None = None) -> dict:
        if not key.strip() or not actor.strip():
            raise ValueError('key and actor are required')
        root = self.workspace.management_repository(project)
        self.writer.validate_path(root, path)
        intent = dict(id=str(uuid4()), project=project, path=path, key=key, actor=actor,
                      source_run=source_run, digest=hashlib.sha256(body.encode()).hexdigest(),
                      state='pending', revision=None, error=None)
        self.repository.save(intent)
        return intent

    def publish(self, intent: dict, body: str) -> dict:
        root = self.workspace.management_repository(intent['project'])
        with self.writer.lock(root):
            return self.commit(root, intent, body)

    def commit(self, root: str, intent: dict, body: str) -> dict:
        try:
            revision = self.writer.commit(root, intent, body)
        except Exception as error:
            try:
                return self.recover(intent, root, str(error))
            except Exception:
                self.finish(intent, None, str(error))
                raise error
        return self.finish(intent, revision, None)

    def write(self, project: str, path: str, body: str, *, key: str, actor: str,
              source_run: str | None = None) -> dict:
        root = self.workspace.management_repository(project)
        self.writer.validate_path(root, path)
        digest = hashlib.sha256(body.encode()).hexdigest()
        with self.writer.lock(root):
            for pending in self.repository.list():
                if pending['project'] == project and pending['state'] == 'pending':
                    self.recover(pending, root)
            previous = self.repository.by_key(project, key)
            if previous is not None:
                if (previous['path'], previous['digest'], previous['actor'], previous['source_run']) != (path, digest, actor, source_run):
                    raise ValueError('idempotency key payload changed')
                return self.recover(previous, root)
            intent = self.prepare(project, path, body, key=key, actor=actor, source_run=source_run)
            return self.commit(root, intent, body)

    def finish(self, intent: dict, revision: str | None, error: str | None) -> dict:
        result = dict(intent, state='confirmed' if revision is not None else 'failed',
                      revision=revision, error=error)
        self.repository.save(result)
        return result

    def recover(self, intent: dict, root: str, error: str = 'interrupted before commit') -> dict:
        if intent['state'] != 'pending':
            return intent
        revision = self.writer.find(root, intent)
        return self.finish(intent, revision, None if revision is not None else error)

    def reconcile(self) -> None:
        for intent in self.repository.list():
            if intent['state'] == 'pending':
                root = self.workspace.management_repository(intent['project'])
                with self.writer.lock(root):
                    current = self.repository.by_key(intent['project'], intent['key'])
                    self.recover(current, root)
