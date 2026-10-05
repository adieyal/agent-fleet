"""Serves the deck dashboard and pushes live fleet state to it over server-sent events.

Each host has one long-lived `fleetd stream` over ssh; job changes arrive as they
happen and are fanned out to every connected browser.
"""
from __future__ import annotations

import atexit
import json
import select
import socket
import time
import webbrowser
from collections.abc import Callable
from contextlib import ExitStack
from dataclasses import asdict
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, unquote, urlsplit

from fleet.api import (
    FOCUSES,
    AlreadyHoused,
    AlreadyShuttered,
    FleetError,
    GuidanceConflict,
    Host,
    ItemResolved,
    NotShuttered,
    NoVacancy,
)
from fleet.container import Container

from fleet_web.documents import (
    AssetNotImage,
    AssetTooLarge,
    DocumentAccessDenied,
    render_document,
    render_markdown,
)
from fleet_web.pages import directive_html, page_document, page_index
from fleet_web.fixture import FixtureLibrary
from fleet_web.library import ProjectLibrary
from fleet_web.resources import build_id as resource_build_id
from fleet_web.resources import checkout_folders, resources, static_directory

_resource_stack = ExitStack()
atexit.register(_resource_stack.close)
WEB_ROOT = _resource_stack.enter_context(static_directory())
INDEX_PATH = WEB_ROOT / "index.html"
APP_DIRECTORIES = ("css", "js")  # the deck's own code, read at startup together with the page
STATIC_PREFIXES = ("/vendor/", "/assets/", "/prototype/")
PROTOTYPES = {"/prototype/bakeoff": "/prototype/bakeoff.html",  # art prototypes; not linked from the deck
              "/prototype/bench": "/prototype/bench.html",
              "/prototype/world": "/prototype/world.html",
              "/prototype/kit": "/prototype/kit.html",
              "/prototype/floor": "/prototype/floor.html",
              "/prototype/robot": "/prototype/robot.html"}


BUILD = resource_build_id(WEB_ROOT)
# Source-checkout folders the art prototypes read; absent from an installed package, so they 404 there.
CHECKOUT_FOLDERS = checkout_folders()
STATIC_TYPES = {".js": "text/javascript; charset=utf-8", ".css": "text/css; charset=utf-8",
                ".html": "text/html; charset=utf-8", ".hdr": "image/vnd.radiance",
                ".glb": "model/gltf-binary", ".gltf": "model/gltf+json",
                ".png": "image/png", ".jpg": "image/jpeg", ".webp": "image/webp", ".json": "application/json",
                ".md": "text/markdown; charset=utf-8", ".txt": "text/plain; charset=utf-8"}
ASSET_POLICY = "default-src 'none'; img-src data:; style-src 'unsafe-inline'; sandbox"
ATTENTION_ACTIONS = ("acknowledge", "snooze", "reopen", "resolve", "undo-resolve", "delegate", "take")
REFUSAL_ACTIONS = ("allow", "dismiss")   # a job step's permission refusals
JOB_ACTIONS = ("answer",)   # a blocked job step's question
GUIDANCE_CHANGES = ("/api/guidance", "/api/guidance/promote")
FLOOR_CHANGES = ("/api/move-in", "/api/link", "/api/merge", "/api/shutter", "/api/restore")
SSE_PING_INTERVAL = 10
SSE_COALESCE = 0.1  # batch bursts of changes into one push












def make_handler(state: Any,
                 library: ProjectLibrary | FixtureLibrary | None = None) -> type[BaseHTTPRequestHandler]:
    container = state.container
    static = resources(container)
    read_static = static.read_static
    # Read once so a running server keeps serving the page and code that match its API.
    index_page = read_static("index.html")
    app_files = static.app_files(WEB_ROOT, APP_DIRECTORIES)
    library = library or ProjectLibrary({}, container=container)

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802 — http.server naming
            path = self.path.split("?", 1)[0]
            if path.startswith("/pages/") or path.startswith("/api/pages/") or path == "/api/pages":
                self.pages_view(path)
            elif path == "/api/stream":
                self.stream()
            elif path == "/api/history/runs" or path.startswith("/api/runs/"):
                self.run_history(path)
            elif path == "/api/doc":
                self.document()
            elif path == "/api/doc/asset":
                self.asset("host", "job")
            elif path == "/api/library/asset":
                self.asset("project")
            elif path == "/api/library":
                try:
                    document = state.library_document(library)
                except ValueError as error:
                    self.respond(400, "application/json", json.dumps({"error": str(error)}).encode())
                    return
                self.respond(200, "application/json", json.dumps(document).encode())
            elif path == "/api/library/doc":
                self.library_document()
            elif path == "/api/library/overview":
                try:
                    projects = state.library_overview(library)
                except ValueError as error:
                    self.respond(400, "application/json", json.dumps({"error": str(error)}).encode())
                    return
                self.respond(200, "application/json", json.dumps({"projects": projects}).encode())
            elif path in ("/api/library/job", "/api/library/working"):
                self.stored_document(path.rsplit("/", 1)[1])
            elif path == "/api/move-in":
                self.move_in_options()
            elif path == "/api/state":
                self.respond(200, "application/json", json.dumps({**state.document(), "build": BUILD}).encode())
            elif path == "/api/decision":
                query = parse_qs(urlsplit(self.path).query)
                if "id" not in query:
                    self.error(400, "the item's id is required")
                    return
                try:
                    detail = container.decision_detail(state=state, item_id=query["id"][0])
                except LookupError as error:
                    self.error(404, str(error))
                    return
                self.respond(200, "application/json", json.dumps(detail, default=str).encode())
            elif path == "/api/bench":
                query = parse_qs(urlsplit(self.path).query)
                if "project" not in query:
                    self.error(400, "project is required")
                    return
                projection = container.project_status(project=query["project"][0])
                try:
                    result = (container.bench_state(projection, query["slice"][0]) if "slice" in query
                              else container.bench_rooms(projection, state.live_jobs()))
                except ValueError as error:
                    self.error(404, str(error))
                    return
                self.respond(200, "application/json", json.dumps(result).encode())
            elif path == "/api/guidance":
                self.guidance()
            elif path == "/api/decisions":
                self.decisions()
            elif path == "/api/history":
                self.history()
            elif path in ("/", "/index.html"):
                self.respond(200, "text/html; charset=utf-8", index_page)
            elif path in PROTOTYPES:
                self.static_file(PROTOTYPES[path])
            elif path in app_files:
                self.respond(200, STATIC_TYPES.get(Path(path).suffix, "application/octet-stream"), app_files[path])
            elif path.startswith(STATIC_PREFIXES):
                self.static_file(path)
            elif path.startswith(tuple(CHECKOUT_FOLDERS)):
                self.checkout_file(path)
            else:
                self.respond(404, "text/plain", b"not found")

        def run_history(self, path: str) -> None:
            try:
                if path == "/api/history/runs":
                    query = parse_qs(urlsplit(self.path).query, keep_blank_values=True)
                    allowed = {"project", "work_item", "descendants", "host", "status", "kind", "unlinked", "since", "until", "limit"}
                    if query.keys() - allowed or any(len(values) != 1 for values in query.values()):
                        raise ValueError("unknown or repeated history filter")
                    filters = {key: values[0] for key, values in query.items()}
                    for key in ("unlinked", "descendants"):
                        if key in filters:
                            if filters[key] not in ("true", "false"):
                                raise ValueError(f"{key} must be true or false")
                            filters[key] = filters[key] == "true"
                    if "limit" in filters:
                        filters["limit"] = int(filters["limit"])
                    result = container.live_history_runs(self=state, filters=filters)
                else:
                    identity = path.removeprefix("/api/runs/")
                    if not identity or "/" in identity:
                        raise LookupError("a stored run ID is required")
                    result = container.live_history_detail(self=state, identity=identity)
            except LookupError as error:
                self.error(404, str(error))
                return
            except (ValueError, FleetError) as error:
                self.error(400, str(error))
                return
            self.respond(200, "application/json", json.dumps(result).encode())

        def do_POST(self) -> None:  # noqa: N802 — http.server naming
            path = self.path.split("?", 1)[0]
            if path.startswith('/api/pages/') and path.rsplit('/', 1)[-1] in ('comments', 'comment-text', 'answer', 'resolve'):
                self.page_write(path)
                return
            action = path.removeprefix("/api/attention/") if path.startswith("/api/attention/") else None
            if (path not in FLOOR_CHANGES + GUIDANCE_CHANGES + ("/api/focus", "/api/decision/answer", "/api/agent/move")
                    and action not in ATTENTION_ACTIONS + REFUSAL_ACTIONS + JOB_ACTIONS):
                self.respond(404, "text/plain", b"not found")
            elif not self.same_origin():
                self.respond(403, "application/json", b'{"error": "cross-origin writes are refused"}')
            elif self.headers.get("Content-Type", "").split(";")[0].strip() != "application/json":
                self.respond(415, "application/json", b'{"error": "send JSON"}')
            else:
                try:
                    body = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)))
                except ValueError:
                    body = None
                body = body if isinstance(body, dict) else {}
                if path == "/api/decision/answer":
                    self.answer(body)
                elif action in REFUSAL_ACTIONS:
                    self.refusals(action, body)
                elif action in JOB_ACTIONS:
                    self.answer_blocked(body)
                elif action:
                    self.attention(action, body)
                elif path in ("/api/move-in", "/api/link"):
                    self.move_in(path.removeprefix("/api/"), body)
                elif path == "/api/merge":
                    self.merge(body)
                elif path == "/api/agent/move":
                    self.move_agent(body)
                elif path in GUIDANCE_CHANGES:
                    self.change_guidance(path, body)
                elif path in ("/api/shutter", "/api/restore"):
                    self.storehouse(path.removeprefix("/api/"), body)
                else:
                    self.focus(body)

        def page_write(self, path: str) -> None:
            origin = self.headers.get('Origin')
            if origin != f'http://{self.headers.get("Host")}':
                self.error(403, 'an exact same-origin browser write is required')
                return
            if self.headers.get('Content-Type', '').split(';')[0].strip() != 'application/json':
                self.error(415, 'send JSON')
                return
            try:
                length = int(self.headers.get('Content-Length', '0'))
                if not 0 < length <= 40 * 1024:
                    self.error(413, 'page write exceeds 40 KiB or is empty')
                    return
                body = json.loads(self.rfile.read(length))
                parts = path.strip('/').split('/')
                if len(parts) != 5 or not isinstance(body, dict):
                    raise ValueError('project, slug and JSON object are required')
                operation = 'comment' if parts[-1] == 'comments' else parts[-1].replace('-', '_')
                allowed = ({'revision', 'comment_id', 'headline', 'body', 'selector', 'reason', 'owner', 'parent'}
                           if operation == 'comment' else {'item_id', 'answer'})
                if operation == 'comment_text':
                    allowed = {'revision', 'comment_id', 'body', 'selector', 'owner', 'parent'}
                elif operation == 'resolve':
                    allowed = {'item_id'}
                required = allowed - {'owner', 'parent'} if operation in ('comment', 'comment_text') else allowed
                if body.keys() - allowed or required - body.keys():
                    raise ValueError('unknown or missing page write fields')
                result = container.page_change(project=unquote(parts[2]), slug=unquote(parts[3]),
                                               operation=operation, actor='user', **body)
            except LookupError as error:
                self.error(404, str(error))
                return
            except (ValueError, FleetError) as error:
                self.error(400, str(error))
                return
            state.bump()
            self.respond(200, 'application/json', json.dumps(result, default=str).encode())

        def pages_view(self, path: str) -> None:
            query = parse_qs(urlsplit(self.path).query)
            api = path.startswith('/api/')
            parts = path.removeprefix('/api').strip('/').split('/')
            if path == '/api/pages':
                parts = ['pages', query.get('project', [''])[0]]
            if len(parts) not in (2, 3) or not parts[1]:
                self.error(400, 'project and optional page slug are required')
                return
            try:
                view = container.page_view(project=unquote(parts[1]),
                    slug=unquote(parts[2]) if len(parts) == 3 else None,
                    revision=query.get('revision', [None])[0])
            except LookupError as error:
                self.error(404, str(error))
                return
            except (ValueError, FleetError) as error:
                self.error(400, str(error))
                return
            if api:
                if len(parts) == 3:
                    view['directive_html'] = {str(index): directive_html(node)
                        for index, node in enumerate(view['nodes']) if node['kind'] != 'prose'}
                self.respond(200, 'application/json', json.dumps(view, default=str).encode())
            else:
                html = page_document(view) if len(parts) == 3 else page_index(view)
                self.respond(200, 'text/html; charset=utf-8', html.encode(), headers={
                    'Content-Security-Policy': "default-src 'none'; script-src 'self'; connect-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self'; base-uri 'none'; frame-ancestors 'none'"})

        def same_origin(self) -> bool:
            """Browsers send Origin on every POST; a page from another site must not change anything."""
            origin = self.headers.get("Origin")
            return origin is None or urlsplit(origin).netloc == self.headers.get("Host")

        def error(self, status: int, message: str) -> None:
            self.respond(status, "application/json", json.dumps({"error": message}).encode())

        def guidance(self) -> None:
            """GET /api/guidance?project=&epic=&version= — the constitution, or the epic's charter, rendered with its
            history; an older version with version=N."""
            query = {key: values[0] for key, values in parse_qs(urlsplit(self.path).query).items()}
            if not query.get("project") or not query.get("version", "1").isdecimal():
                self.error(400, "project is required, and version is a number")
                return
            number = int(query["version"]) if "version" in query else None
            self.guidance_result(lambda: container.guidance_view(project=query["project"], epic=query.get("epic"), number=number), render=True)

        def decisions(self) -> None:
            """GET /api/decisions?project= or ?epic= — newest first; epic lists include charter promotion state."""
            query = parse_qs(urlsplit(self.path).query)
            epic = (query.get('epic') or [''])[0]
            project = (query.get('project') or [''])[0]
            if bool(epic) == bool(project):
                self.error(400, "exactly one of project or epic is required")
                return
            self.guidance_result(lambda: container.epic_decisions(epic=epic) if epic else container.project_decisions(project=project))

        def history(self) -> None:
            """GET /api/history?subject=&since= — the subject's audit trail, newest first, as `fleet history --json`
            prints it; subject takes an id, a unique id prefix or a subject such as attention:<id>."""
            query = {key: values[0] for key, values in parse_qs(urlsplit(self.path).query).items()}
            if not query.get("subject", "").strip():
                self.error(400, "subject is required")
                return
            try:
                since = Container().parse_since(query["since"]) if query.get("since") else None
            except ValueError as error:
                self.error(400, str(error))
                return
            try:
                result = container.subject_history(reference=query["subject"], since=since)
            except LookupError as error:
                self.error(404, str(error))
                return
            self.respond(200, "application/json", json.dumps(result).encode())

        def change_guidance(self, path: str, body: dict[str, Any]) -> None:
            """POST /api/guidance {"project", "epic" (null for the constitution), "markdown", "base": the version the
            editor opened, 0 for none} — a new version by web-user; refused with 409 if another version came first.
            POST /api/guidance/promote {"epic", "decision"} — the decision added to the charter's decisions in force."""
            if path == "/api/guidance/promote":
                if not isinstance(body.get("epic"), str) or not isinstance(body.get("decision"), str):
                    self.error(400, "epic and decision are required")
                    return

                self.guidance_result(lambda: container.promote_guidance(epic=body['epic'], decision=body['decision']), render=True)
                return
            epic = body.get("epic")
            if (not isinstance(body.get("project"), str) or not isinstance(body.get("markdown"), str)
                    or not isinstance(epic, (str, type(None))) or type(body.get("base")) is not int):
                self.error(400, "project, markdown and base (a version number) are required")
                return

            self.guidance_result(lambda: container.write_guidance(project=body['project'], markdown=body['markdown'],
                                 epic=epic, base=body['base']), render=True)

        def guidance_result(self, produce: Callable[[], dict[str, Any]], *, render=False) -> None:
            try:
                result = produce()
                if render and result['guidance'] is not None:
                    result = {**result, **render_markdown(result['markdown'])}
            except GuidanceConflict as error:
                self.error(409, str(error))
            except LookupError as error:
                self.error(404, str(error))
            except ValueError as error:
                self.error(400, str(error))
            else:
                self.respond(200, "application/json", json.dumps(result, default=str).encode())

        def focus(self, body: dict[str, Any]) -> None:
            """POST /api/focus {"focus": "priority" | "background", "projects": [id, …], "labels": [label, …]}

            Projects are focused by ID; labels are for jobs and sessions with no linked project.
            """
            project_ids, labels = body.get("projects", []), body.get("labels", [])
            if (body.get("focus") not in FOCUSES or not isinstance(project_ids, list) or not isinstance(labels, list)
                    or not all(isinstance(name, str) for name in project_ids + labels) or not project_ids + labels):
                self.error(400, "focus (priority or background) and some projects or labels are required")
                return
            try:
                state.set_focus(body["focus"], project_ids, labels)
            except FleetError as error:
                self.error(400, str(error))
                return
            self.respond(200, "application/json", json.dumps(state.focus_snapshot()).encode())

        def move_in_options(self) -> None:
            """GET /api/move-in?label=&host=&host=… — projects the label on those hosts may belong to, to offer
            linking before a new project."""
            query = parse_qs(urlsplit(self.path).query)
            try:
                options = state.move_in_options((query.get("label") or [""])[0], query.get("host", []))
            except FleetError as error:
                self.error(400, str(error))
                return
            self.respond(200, "application/json", json.dumps(options).encode())

        def move_in(self, action: str, body: dict[str, Any]) -> None:
            """POST /api/move-in {"hosts": [host, …], "label": label, "shutter": project ID to make room, if full}
            — register the label on those hosts as one project on the lowest free floor.
            POST /api/link {"project": ID, "hosts": [host, …], "label": label} — link it to that project instead.
            A single "host" may stand for "hosts"."""
            hosts = body.get("hosts", [body["host"]] if "host" in body else [])
            if (not isinstance(hosts, list) or not all(isinstance(host, str) for host in hosts)
                    or not isinstance(body.get("label"), str) or not isinstance(body.get("shutter", ""), str)
                    or (action == "link" and not isinstance(body.get("project"), str))):
                self.error(400, "hosts and a label are required" + (", and a project" if action == "link" else ""))
                return
            if action == "link":
                self.change_floors(lambda: state.link_in(body["project"], hosts, body["label"]))
            else:
                self.change_floors(lambda: state.move_in(hosts, body["label"], body.get("shutter")))

        def move_agent(self, body: dict[str, Any]) -> None:
            """POST /api/agent/move {"host": host, "id": job or session id, "project": ID} — move it to that project."""
            if not all(isinstance(body.get(key), str) for key in ("host", "id", "project")):
                self.error(400, "host, id and project are required")
                return
            self.change_floors(lambda: state.move_agent(body["host"], body["id"], body["project"]))

        def merge(self, body: dict[str, Any]) -> None:
            """POST /api/merge {"keep": ID, "other": ID} — fold a project registered by mistake into the older one."""
            if not isinstance(body.get("keep"), str) or not isinstance(body.get("other"), str):
                self.error(400, "keep and other are required")
                return
            self.change_floors(lambda: state.merge(body["keep"], body["other"]))

        def storehouse(self, action: str, body: dict[str, Any]) -> None:
            """POST /api/shutter {"project": ID} — pack it away and free its floor.
            POST /api/restore {"project": ID, "shutter": project ID to make room, if full} — move its crate back in."""
            if not isinstance(body.get("project"), str) or not isinstance(body.get("shutter", ""), str):
                self.error(400, "project is required")
                return
            if action == "shutter":
                self.change_floors(lambda: state.shutter(body["project"]))
            else:
                self.change_floors(lambda: state.restore(body["project"], body.get("shutter")))

        def change_floors(self, change: Callable[[], dict[str, Any]]) -> None:
            try:
                changed = change()
            except LookupError as error:
                self.error(404, str(error.args[0]))
                return
            except (NoVacancy, AlreadyHoused, AlreadyShuttered, NotShuttered) as error:
                self.error(409, str(error))
                return
            except FleetError as error:
                self.error(400, str(error))
                return
            self.respond(200, "application/json", json.dumps(changed).encode())

        def answer(self, body: dict[str, Any]) -> None:
            if not isinstance(body.get("id"), str) or not isinstance(body.get("answer"), str):
                self.error(400, "item id and answer are required")
                return
            try:
                decision = state.answer_decision(body["id"], body["answer"])
            except LookupError as error:
                self.error(404, str(error))
                return
            except (FleetError, ValueError) as error:
                self.error(400, str(error))
                return
            self.respond(200, "application/json", json.dumps(asdict(decision), default=str).encode())

        def refusals(self, action: str, body: dict[str, Any]) -> None:
            """POST /api/attention/allow {"id": item id, "scope": "refused" | "bash"} — add permission rules to
            the job on its worker and continue the refused step there.
            POST /api/attention/dismiss {"id": item id} — resolve the batch and change nothing."""
            if not isinstance(body.get("id"), str) or (action == "allow" and not isinstance(body.get("scope"), str)):
                self.error(400, "the item's id is required" + (", and a scope" if action == "allow" else ""))
                return
            try:
                result = state.refusal_action(action, body['id'], body.get('scope'))
            except LookupError as error:
                self.error(404, str(error.args[0]))
                return
            except ItemResolved as error:
                self.error(409, str(error))
                return
            except (FleetError, ValueError, RuntimeError) as error:
                self.error(400, str(error))
                return
            self.respond(200, "application/json", json.dumps(result).encode())

        def answer_blocked(self, body: dict[str, Any]) -> None:
            """POST /api/attention/answer {"id": item id, "answer": reply, "work_item"?: id} — add the reply as a
            step to the blocked job on its worker, serving work_item or else the blocked step's work, and resolve
            the item."""
            if not isinstance(body.get("id"), str) or not isinstance(body.get("answer"), str):
                self.error(400, "the item's id and an answer are required")
                return
            if not isinstance(body.get("work_item"), (str, type(None))):
                self.error(400, "work_item must be a work item ID")
                return
            try:
                result = state.answer_blocked(body['id'], body['answer'], body.get('work_item'))
            except LookupError as error:
                self.error(404, str(error.args[0]))
                return
            except ItemResolved as error:
                self.error(409, str(error))
                return
            except (FleetError, ValueError, RuntimeError) as error:
                self.error(400, str(error))
                return
            self.respond(200, "application/json", json.dumps(result).encode())

        def attention(self, action: str, body: dict[str, Any]) -> None:
            """POST /api/attention/acknowledge|snooze|reopen|resolve {"id": item id, "seconds": snooze length}"""
            if not isinstance(body.get("id"), str):
                self.error(400, "the item's id is required")
                return
            try:
                result = state.act_on_attention(action, body["id"], body.get("seconds"), body.get("undo"))
            except LookupError as error:
                self.error(404, str(error.args[0]))
                return
            except ItemResolved as error:
                self.error(409, str(error))
                return
            except (FleetError, ValueError) as error:
                self.error(400, str(error))
                return
            self.respond(200, "application/json", json.dumps({"id": body["id"], "action": action, **result}).encode())

        def static_file(self, path: str) -> None:
            """Vendored libraries and 3D assets; anything resolving outside those folders is refused."""
            target = (WEB_ROOT / unquote(path).lstrip("/")).resolve()
            allowed = any(target.is_relative_to(WEB_ROOT / prefix.strip("/")) for prefix in STATIC_PREFIXES)
            if not allowed or not target.is_file():
                self.respond(404, "text/plain", b"not found")
                return
            content_type = STATIC_TYPES.get(target.suffix.lower(), "application/octet-stream")
            self.respond(200, content_type, read_static(target.relative_to(WEB_ROOT).as_posix()), cache_seconds=3600)

        def checkout_file(self, path: str) -> None:
            """Art and concept files from a source checkout, for the prototypes; nothing outside those folders."""
            prefix = next(p for p in CHECKOUT_FOLDERS if path.startswith(p))
            folder = CHECKOUT_FOLDERS[prefix].resolve()
            target = (folder / unquote(path.removeprefix(prefix))).resolve()
            if not target.is_relative_to(folder) or not target.is_file():
                self.respond(404, "text/plain", b"not found")
                return
            content_type = STATIC_TYPES.get(target.suffix.lower(), "application/octet-stream")
            self.respond(200, content_type, target.read_bytes(), cache_seconds=3600)

        def document(self) -> None:
            """GET /api/doc?host=&job=&id= — one rendered document; ids come from a job's `documents` list."""
            query = {key: values[0] for key, values in parse_qs(urlsplit(self.path).query).items()}
            if query.get("host") not in state.host_names() or not query.get("job") or not query.get("id"):
                self.respond(400, "application/json", b'{"error": "host, job and id are required"}')
                return
            try:
                body = render_document(state.read_document(query["host"], query["job"], query["id"]), job_id=query["job"])
            except DocumentAccessDenied as error:
                self.respond(403, "application/json", json.dumps({"error": str(error)}).encode())
                return
            except FleetError as error:
                self.respond(404, "application/json", json.dumps({"error": str(error)}).encode())
                return
            self.respond(200, "application/json", json.dumps(body).encode())

        def asset(self, *owner: str) -> None:
            """GET /api/doc/asset?host=&job=&id=&path= or /api/library/asset?project=&id=&path= — an image
            a document links to, resolved beside the document under the document's own roots."""
            query = {key: values[0] for key, values in parse_qs(urlsplit(self.path).query).items()}
            if not all(query.get(key) for key in (*owner, "id", "path")) or (
                    "host" in owner and query["host"] not in state.host_names()):
                self.error(400, f"{', '.join(owner)}, id and path are required")
                return
            try:
                if "host" in owner:
                    found = state.read_asset(query["host"], query["job"], query["id"], query["path"])
                else:
                    found = library.read_asset(query["project"], query["id"], query["path"])
            except DocumentAccessDenied as error:
                self.error(403, str(error))
                return
            except AssetNotImage as error:
                self.error(415, str(error))
                return
            except AssetTooLarge as error:
                self.error(413, str(error))
                return
            except FleetError as error:
                self.error(404, str(error))
                return
            if found is None:
                self.error(404, "image not found")
                return
            content_type, content = found
            # an SVG can carry script: the policy keeps it inert even if opened on its own
            self.respond(200, content_type, content, headers={"Content-Security-Policy": ASSET_POLICY,
                                                              "X-Content-Type-Options": "nosniff",
                                                              "Cache-Control": "private, max-age=86400" if query.get("v") else "max-age=60"})

        def library_document(self) -> None:
            """GET /api/library/doc?project=&id= — Markdown under a configured local root."""
            query = {key: values[0] for key, values in parse_qs(urlsplit(self.path).query).items()}
            if not query.get("project") or not query.get("id"):
                self.respond(400, "application/json", b'{"error": "project and id are required"}')
                return
            body = library.read(query["project"], query["id"])
            if body is None:
                self.respond(404, "application/json", b'{"error": "document not found"}')
                return
            self.respond(200, "application/json", json.dumps(body).encode())

        def stored_document(self, section: str) -> None:
            """GET /api/library/job?project=&job=&id= or /api/library/working?project=&id= — from the project's store,
            so it reads whether or not the job is still on its host."""
            query = {key: values[0] for key, values in parse_qs(urlsplit(self.path).query).items()}
            if not query.get("project") or not query.get("id") or (section == "job" and not query.get("job")):
                self.respond(400, "application/json", b'{"error": "project, id and (for a job) job are required"}')
                return
            body = state.stored_document(section, query["project"], query["id"], query.get("job"))
            if body is None:
                self.respond(404, "application/json", b'{"error": "document not in the project store"}')
                return
            self.respond(200, "application/json", json.dumps({**body, **render_markdown(body["markdown"])}).encode())

        def stream(self) -> None:
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Accel-Buffering", "no")
            self.end_headers()
            version, pipeline_seq = -1, 0
            try:
                while True:
                    new_version = state.wait_for_change(version, timeout=SSE_PING_INTERVAL, seen_pipelines=pipeline_seq)
                    # A reader may close while waiting. Detect its FIN before reading state again.
                    if select.select([self.connection], [], [], 0)[0] and not self.connection.recv(1, socket.MSG_PEEK):
                        return
                    if new_version != version:
                        time.sleep(SSE_COALESCE)
                        version, pipeline_seq = state.version, state.pipeline_seq
                        payload = json.dumps({**state.document(), "build": BUILD, "version": version})   # carries every pipeline as it is now
                        self.wfile.write(f"event: state\ndata: {payload}\n\n".encode())
                    elif state.pipeline_seq != pipeline_seq:
                        updates = state.pipeline_updates(pipeline_seq)
                        pipeline_seq = max([pipeline_seq] + [update["seq"] for update in updates])
                        for update in updates:
                            self.wfile.write(f"event: pipeline\ndata: {json.dumps(update)}\n\n".encode())
                    else:
                        self.wfile.write(b"event: ping\ndata: {}\n\n")
                    self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError):
                return

        def respond(self, status: int, content_type: str, body: bytes, *, cache_seconds: int = 0,
                    headers: dict[str, str] | None = None) -> None:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            for name, value in (headers or {}).items():
                self.send_header(name, value)
            if "Cache-Control" not in (headers or {}):
                self.send_header("Cache-Control", f"max-age={cache_seconds}" if cache_seconds else "no-store")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, format: str, *arguments: Any) -> None:  # noqa: A002 — silence access log
            pass

    return Handler


def serve(hosts: list[Host], *, port: int, bind: str, open_browser: bool = False,
          libraries: dict[str, Any] | None = None, project_labels: dict[str, str] | None = None,
          pipelines: dict[str, dict[str, str]] | None = None, container=None) -> None:
    container = Container() if container is None else container
    state = container.live_state(hosts=hosts, project_labels=project_labels, pipelines=pipelines)
    runtime = container.start_live(state=state)
    try:
        run_server(make_handler(state, ProjectLibrary(libraries or {}, container=container)), port=port, bind=bind, open_browser=open_browser)
    finally:
        runtime.close()


def serve_fixture(path: str, *, port: int, bind: str, open_browser: bool = False, container=None) -> None:
    """Serve a recorded fleet from JSON (see fleet_web.fixture); no hosts are contacted."""
    container = Container() if container is None else container
    state = container.fixture_state(fixture=container.fixture_data(path=path))
    run_server(make_handler(state, FixtureLibrary(state.fixture, container=state.container)), port=port, bind=bind, open_browser=open_browser)


def run_server(handler: type[BaseHTTPRequestHandler], *, port: int, bind: str, open_browser: bool) -> None:
    server = ThreadingHTTPServer((bind, port), handler)
    server.daemon_threads = True
    url = f"http://{'localhost' if bind in ('127.0.0.1', '0.0.0.0') else bind}:{port}/"
    print(f"fleet deck at {url}  (demo: {url}?demo)", flush=True)
    if open_browser:
        webbrowser.open(url)
    try:
        server.serve_forever()
    finally:
        server.server_close()


def build_id(root: Path = WEB_ROOT) -> str:
    return resource_build_id(root)
