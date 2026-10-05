"""Narrow collaborators for live and recorded read models."""
from dataclasses import dataclass
from typing import Callable

from fleet.modules.attention import AttentionFacade
from fleet.modules.execution import ExecutionFacade
from fleet.modules.workspace import WorkspaceFacade
from fleet.projections.overview import Overview


@dataclass(frozen=True)
class LiveReaders:
    attention: AttentionFacade
    execution: ExecutionFacade
    workspace: WorkspaceFacade
    revision: Callable[[], object]
    run_work: Callable[[], dict]
    building: Callable[..., dict]
    history_runs: Callable[..., dict]
    run_detail: Callable[..., dict]
    overview: Overview
