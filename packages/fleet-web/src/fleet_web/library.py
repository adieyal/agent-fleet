"""Render documents supplied by the container's local library reader."""
from pathlib import Path
import json
from typing import Any

from fleet.container import is_private
from fleet_web.documents import render_markdown

def prd_markdown(prd: dict[str, Any], name: str) -> str:
    """A Ralph prd.json as a page: status, objectives, stories with pass marks, open questions, decisions."""
    def items(value: Any) -> list[str]:
        if value is None:
            return []
        return [str(item) for item in value] if isinstance(value, list) else [str(value)]

    stories = prd.get("userStories") or []
    passing = sum(1 for story in stories if story.get("passes"))
    lines = [f"# {name}", ""]
    if prd.get("status"):
        lines += [f"**Status.** {prd['status']}", ""]
    where = [f"branch `{prd['branchName']}`" if prd.get("branchName") else "",
             f"off `{prd['baseBranch']}`" if prd.get("baseBranch") else "",
             f"worktree `{prd['worktree']}`" if prd.get("worktree") else ""]
    if any(where):
        lines += ["**Where.** " + " ".join(part for part in where if part), ""]
    if prd.get("description"):
        lines += [str(prd["description"]), ""]
    for heading, key in (("Objectives", "objectives"),):
        if items(prd.get(key)):
            lines += [f"## {heading}", "", *(f"- {item}" for item in items(prd.get(key))), ""]
    if stories:
        lines += [f"## Stories · {passing} of {len(stories)} passing", ""]
        for story in stories:
            mark = "x" if story.get("passes") else " "
            extra = " (blocked)" if story.get("blocked") else ""
            lines.append(f"- [{mark}] **{story.get('id', '?')}** {story.get('title', '')}{extra}")
        lines.append("")
    for heading, key in (("Open questions", "openQuestions"), ("Decisions", "decisions"), ("Plan", "plan"),
                         ("Out of scope", "outOfScope")):
        if items(prd.get(key)):
            lines += [f"## {heading}", "", *(f"- {item}" for item in items(prd.get(key))), ""]
    return "\n".join(lines)


class ProjectLibrary:
    def __init__(self, roots, *, container):
        self.reader = container.project_library(roots=roots)
        self.roots = self.reader.roots

    def root(self, project):
        return self.reader.root(project)

    def list(self):
        return self.reader.list()

    def read_asset(self, project, document_id, asset_path):
        return self.reader.read_asset(project, document_id, asset_path)

    def read(self, project, document_id):
        document = self.reader.read(project, document_id)
        if document is None:
            return None
        text = document.pop('content')
        if document['kind'] == 'prd':
            try:
                prd = json.loads(text)
            except ValueError as error:
                text = f"# {document_id}\n\nThis prd.json is not valid JSON: {error}"
            else:
                text = prd_markdown(prd, document_id) if isinstance(prd, dict) else f"# {document_id}\n\nNot a PRD object."
        return {**document, **render_markdown(text)}
