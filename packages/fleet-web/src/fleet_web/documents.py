"""Fetches an agent's Markdown document from its host and renders it for the reader view."""
from __future__ import annotations

import shlex
from typing import Any

from markdown_it import MarkdownIt
from mdit_py_plugins.anchors import anchors_plugin
from mdit_py_plugins.footnote import footnote_plugin
from mdit_py_plugins.tasklists import tasklists_plugin

from fleet.container import STATUS_LINE, Host
# Public renderer error/limit exports retained for callers.
from fleet.container import (ASSET_READ_LIMIT as ASSET_READ_LIMIT, IMAGE_TYPES as IMAGE_TYPES,
    AssetNotImage as AssetNotImage, AssetTooLarge as AssetTooLarge,
    DocumentAccessDenied as DocumentAccessDenied, FleetError as FleetError)

WORDS_PER_MINUTE = 230

# html=False escapes any raw HTML in the document, so agent output can't inject markup or scripts.
renderer = (
    MarkdownIt("commonmark", {"html": False, "linkify": False, "typographer": True})
    .enable(["table", "strikethrough"])
    .use(anchors_plugin, min_level=1, max_level=3, permalink=False)
    .use(footnote_plugin)
    .use(tasklists_plugin)
)


def outline(markdown: str) -> list[dict[str, Any]]:
    """Headings (levels 1–3) with the ids anchors_plugin gives them, for a table of contents."""
    entries = []
    tokens = renderer.parse(markdown)
    for index, token in enumerate(tokens):
        if token.type == "heading_open" and token.tag in ("h1", "h2", "h3"):
            entries.append({"level": int(token.tag[1]), "id": token.attrGet("id"),
                            "text": tokens[index + 1].content})
    return entries


def fetch_document(host: Host, job_id: str, document_id: str, *, container) -> dict[str, Any]:
    document = container.read_document(host=host, job_id=job_id, document_id=document_id)
    return render_document({**document, "host": host.name}, job_id=job_id)


def render_document(document, *, job_id):
    document = document.copy()
    markdown = STATUS_LINE.sub("", document.pop("content")).strip()
    if document.get("media") == "file":
        command = shlex.join(["fleet", "pull", f"{document['host']}:{job_id}"])
        markdown = "The reader cannot preview this file type. It is listed for collection.\n\n" \
                   + f"Copy the job’s outbox to your local fleet-{job_id} directory with:\n\n```sh\n{command}\n```"
    return {**document, **render_markdown(markdown)}


def render_markdown(markdown: str) -> dict[str, Any]:
    """Render Markdown for both job documents and local project libraries."""
    words = len(markdown.split())
    return {"markdown": markdown, "html": renderer.render(markdown),
            "toc": outline(markdown), "words": words, "minutes": max(1, round(words / WORDS_PER_MINUTE))}


def fetch_asset(host, job_id, document_id, asset_path, *, container):
    return container.read_asset(host=host, job_id=job_id, document_id=document_id, asset_path=asset_path)
