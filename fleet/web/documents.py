"""Fetches an agent's Markdown document from its host and renders it for the reader view."""
from __future__ import annotations

import base64
import re
import shlex
from pathlib import PurePosixPath
from typing import Any

from markdown_it import MarkdownIt
from mdit_py_plugins.anchors import anchors_plugin
from mdit_py_plugins.footnote import footnote_plugin
from mdit_py_plugins.tasklists import tasklists_plugin

from fleet import transport
from fleet.transport import FleetError, Host

WORDS_PER_MINUTE = 230
STATUS_LINE = re.compile(r"^\s*\**FLEET_STATUS:.*$", re.MULTILINE)

# html=False escapes any raw HTML in the document, so agent output can't inject markup or scripts.
renderer = (
    MarkdownIt("commonmark", {"html": False, "linkify": False, "typographer": True})
    .enable(["table", "strikethrough"])
    .use(anchors_plugin, min_level=1, max_level=3, permalink=False)
    .use(footnote_plugin)
    .use(tasklists_plugin)
)


IMAGE_TYPES = {".svg": "image/svg+xml", ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
               ".gif": "image/gif", ".webp": "image/webp"}
ASSET_READ_LIMIT = 32 * 1024 * 1024  # fleetd's cap for the same images: room for full-resolution contact sheets


class DocumentAccessDenied(FleetError):
    pass


class AssetNotImage(FleetError):
    pass


class AssetTooLarge(FleetError):
    pass


def outline(markdown: str) -> list[dict[str, Any]]:
    """Headings (levels 1–3) with the ids anchors_plugin gives them, for a table of contents."""
    entries = []
    tokens = renderer.parse(markdown)
    for index, token in enumerate(tokens):
        if token.type == "heading_open" and token.tag in ("h1", "h2", "h3"):
            entries.append({"level": int(token.tag[1]), "id": token.attrGet("id"),
                            "text": tokens[index + 1].content})
    return entries


def fetch_document(host: Host, job_id: str, document_id: str) -> dict[str, Any]:
    path = PurePosixPath(document_id)
    if path.is_absolute() or ".." in path.parts:
        raise DocumentAccessDenied(f"document path outside approved document roots: {document_id}")
    try:
        document = transport.call(host, ["read", job_id, document_id], timeout=30)
    except FleetError as error:
        if "document path outside approved document roots" in str(error):
            raise DocumentAccessDenied(str(error)) from error
        raise
    markdown = STATUS_LINE.sub("", document.pop("content")).strip()
    if document.get("media") == "file":
        command = shlex.join(["fleet", "pull", f"{host.name}:{job_id}"])
        markdown = "The reader cannot preview this file type. It is listed for collection.\n\n" \
                   + f"Copy the job’s outbox to your local fleet-{job_id} directory with:\n\n```sh\n{command}\n```"
    return {**document, "host": host.name, **render_markdown(markdown)}


def fetch_asset(host: Host, job_id: str, document_id: str, asset_path: str) -> tuple[str, bytes]:
    """An image a job document links to, as (content type, bytes); fleetd applies the document's roots."""
    document = PurePosixPath(document_id)
    if document.is_absolute() or ".." in document.parts or "\x00" in asset_path:
        raise DocumentAccessDenied(f"asset path outside approved document roots: {asset_path}")
    try:
        result = transport.call(host, ["read-asset", job_id, document_id, asset_path], timeout=30)
    except FleetError as error:
        message = str(error)
        if "outside approved document roots" in message:
            raise DocumentAccessDenied(message) from error
        if "not a supported image type" in message:
            raise AssetNotImage(message) from error
        if "asset larger than" in message:
            raise AssetTooLarge(message) from error
        raise
    return result["type"], base64.b64decode(result["content"])


def render_markdown(markdown: str) -> dict[str, Any]:
    """Render Markdown for both job documents and local project libraries."""
    words = len(markdown.split())
    return {"markdown": markdown, "html": renderer.render(markdown),
            "toc": outline(markdown), "words": words, "minutes": max(1, round(words / WORDS_PER_MINUTE))}
