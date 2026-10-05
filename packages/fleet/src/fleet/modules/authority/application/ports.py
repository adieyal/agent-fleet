from typing import Protocol

from ..domain import Activation


class ActivationRepository(Protocol):
    def get(self, identity: str) -> Activation: ...
    def insert(self, activation: Activation) -> None: ...
