"""Shared connection and transaction scope for SQLite repositories."""

from contextlib import closing, contextmanager
from copy import copy

from .store import Store, UnitOfWork, connect


class Repository:
    def __init__(self, store: Store, unit: UnitOfWork | None = None) -> None:
        self.store, self.unit = store, unit

    @contextmanager
    def transaction(self):
        if self.unit is not None:
            yield self
            return
        with self.store.unit_of_work() as unit:
            bound = copy(self)
            bound.unit = unit
            bound.bind(unit)
            yield bound

    def bind(self, unit: UnitOfWork) -> None:
        pass

    def rows(self, query: str, parameters: tuple = ()) -> list:
        if self.unit is not None:
            return self.unit.connection.execute(query, parameters).fetchall()
        with closing(connect(self.store.path)) as connection:
            return connection.execute(query, parameters).fetchall()
