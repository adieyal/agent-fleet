"""Shared connection and transaction scope for SQLite repositories."""

from contextlib import closing, contextmanager
from copy import copy
from typing import Callable

from .store import Store, UnitOfWork, connect


class Repository:
    def __init__(self, store: Store, unit: UnitOfWork | None = None) -> None:
        self.store, self.unit = store, unit

    @contextmanager
    def transaction(self, *, prepare: Callable[[UnitOfWork], object] | None = None):
        if self.unit is not None:
            if prepare is not None:
                prepare(self.unit)
            yield self
            return
        unit = self.store.unit_of_work()
        bound = copy(self)
        bound.unit = unit
        # Binding builds transaction-local collaborators, not database state. Do
        # that before BEGIN IMMEDIATE so provider locks and graph copies cannot
        # hold up every other writer.
        bound.bind(unit)
        if prepare is not None:
            prepare(unit)
        with unit:
            yield bound

    def bind(self, unit: UnitOfWork) -> None:
        pass

    def rows(self, query: str, parameters: tuple = ()) -> list:
        if self.unit is not None:
            return self.unit.connection.execute(query, parameters).fetchall()
        with closing(connect(self.store.path)) as connection:
            return connection.execute(query, parameters).fetchall()
