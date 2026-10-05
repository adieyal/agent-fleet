"""Public presentation values and errors. Operations belong to container providers."""

from __future__ import annotations

import re as _re
from dataclasses import dataclass as _dataclass
from subprocess import TimeoutExpired

from fleet.errors import FleetError
from fleet.host_values import Host, HostReport
from fleet.modules.attention import (
    KINDS as ATTENTION_KINDS,
)
from fleet.modules.attention import (
    ItemResolved,
)
from fleet.modules.execution import Run
from fleet.modules.records import GuidanceConflict
from fleet.modules.work import (
    CONDITIONS,
    KINDS,
    RELATION_TYPES,
)
from fleet.modules.workspace import (
    FOCUSES,
    MAX_CAPACITY,
    AlreadyHoused,
    AlreadyShuttered,
    NotShuttered,
    NoVacancy,
    Registry,
)

STATUS_LINE = _re.compile(r"^\s*\**FLEET_STATUS:.*$", _re.MULTILINE)

IMAGE_TYPES = {
    ".svg": "image/svg+xml",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".webp": "image/webp",
}
ASSET_READ_LIMIT = (
    32 * 1024 * 1024
)  # fleetd's cap for the same images: room for full-resolution contact sheets


class DocumentAccessDenied(FleetError):
    pass


class AssetNotImage(FleetError):
    pass


class AssetTooLarge(FleetError):
    pass


@_dataclass
class DispatchRequest:
    host: str
    project: str
    description: str
    agent: str
    cwd: str
    work_item: str | None
    permission: str | None
    model: str | None
    id: str | None
    allow: list[str] | None
    add_dir: list[str] | None
    env: list[str] | None
    keep_going: bool
    context: list[str] | None
    hold: bool
    actor: str


__all__ = [
    "ASSET_READ_LIMIT",
    "ATTENTION_KINDS",
    "CONDITIONS",
    "FOCUSES",
    "IMAGE_TYPES",
    "KINDS",
    "MAX_CAPACITY",
    "RELATION_TYPES",
    "STATUS_LINE",
    "AlreadyHoused",
    "AlreadyShuttered",
    "AssetNotImage",
    "AssetTooLarge",
    "DispatchRequest",
    "DocumentAccessDenied",
    "FleetError",
    "GuidanceConflict",
    "Host",
    "HostReport",
    "ItemResolved",
    "NoVacancy",
    "NotShuttered",
    "Registry",
    "Run",
    "TimeoutExpired",
]
