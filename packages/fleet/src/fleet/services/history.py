"""History retention operations."""
from __future__ import annotations


class History:
    def __init__(self, store):
        self.store = store

    def span_before(self, before):
        return self.store.history_span_before(before)

    def prune(self, before, *, actor):
        return self.store.prune_history(before, actor)
