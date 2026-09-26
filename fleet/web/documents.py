"""Fetches an agent's Markdown document from its host and renders it for the reader view."""
from __future__ import annotations

import re
from typing import Any

from markdown_it import MarkdownIt
from mdit_py_plugins.anchors import anchors_plugin
from mdit_py_plugins.footnote import footnote_plugin
from mdit_py_plugins.tasklists import tasklists_plugin

from fleet import transport
from fleet.transport import Host

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
    document = transport.call(host, ["read", job_id, document_id], timeout=30)
    markdown = STATUS_LINE.sub("", document.pop("content")).strip()
    words = len(markdown.split())
    return {**document, "host": host.name, "markdown": markdown, "html": renderer.render(markdown),
            "toc": outline(markdown), "words": words, "minutes": max(1, round(words / WORDS_PER_MINUTE))}
