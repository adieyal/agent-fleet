#!/usr/bin/env python3
"""Explore what Sefaria returns for a topic.

Runs three lookups for a query and shows the results side by side:

  topics  - Sefaria's curated topic pages (e.g. "honoring parents") and their sources
  search  - Sefaria's full-text search (lemmatized, so it matches word forms)
  rerank  - optional: re-orders everything by embedding similarity to your query,
            which is the actual "semantic" step (needs sentence-transformers)

Standard library only, unless you pass --rerank.

    python sefaria_search.py search "honoring parents"
    python sefaria_search.py search "kindness to animals" --json > out.json
    python sefaria_search.py serve            # web UI on http://localhost:8765
"""

from __future__ import annotations

import argparse
import html
import json
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

BASE = "https://www.sefaria.org"
UA = "torah-study-experiment/0.1"
TAG_RE = re.compile(r"<[^>]+>")


def _request(path: str, payload: dict | None = None, timeout: float = 20):
    url = path if path.startswith("http") else BASE + path
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(url, data=data, headers={"User-Agent": UA})
    if data is not None:
        req.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode())


def strip_html(text) -> str:
    if isinstance(text, list):
        text = " ".join(strip_html(t) for t in text)
    return html.unescape(TAG_RE.sub("", text or "")).strip()


# --- Sefaria lookups --------------------------------------------------------


def find_topics(query: str, limit: int = 5) -> list[dict]:
    """Topic slugs whose names match the query, via the autocomplete API."""
    data = _request(f"/api/name/{urllib.parse.quote(query)}?limit=20")
    topics = []
    for obj in data.get("completion_objects", []):
        if obj.get("type") == "Topic" and obj.get("key"):
            topics.append({"slug": obj["key"], "title": obj.get("title", obj["key"])})
        if len(topics) >= limit:
            break
    return topics


def topic_sources(slug: str, limit: int = 15) -> dict:
    """A topic's description and its curated source refs."""
    data = _request(f"/api/v2/topics/{urllib.parse.quote(slug)}?with_refs=1")
    refs = []
    for link_type, group in (data.get("refs") or {}).items():
        for r in group.get("refs", []) if isinstance(group, dict) else []:
            if r.get("is_sheet"):
                continue
            refs.append({"ref": r.get("ref"), "link_type": link_type,
                         "rank": (r.get("order") or {}).get("pr", 0)})
    refs.sort(key=lambda r: r["rank"] or 0, reverse=True)
    title = data.get("primaryTitle") or {}
    desc = data.get("description") or {}
    return {
        "slug": slug,
        "title": title.get("en") or slug,
        "he_title": title.get("he", ""),
        "description": strip_html(desc.get("en", "")),
        "refs": refs[:limit],
    }


def full_text_search(query: str, size: int = 20, field: str = "naive_lemmatizer") -> list[dict]:
    """Sefaria's Elasticsearch text search. field: naive_lemmatizer | exact."""
    payload = {
        "query": query, "type": "text", "field": field, "size": size, "slop": 10,
        "source_proj": True, "filters": [], "filter_fields": [], "aggs": [],
        "sort_method": "score", "sort_fields": ["pagesheetrank"],
        "sort_reverse": False, "sort_score_missing": 0.04,
    }
    data = _request("/api/search-wrapper", payload)
    hits = []
    seen = set()
    for h in (data.get("hits") or {}).get("hits", []):
        src = h.get("_source", {})
        ref = src.get("ref")
        if not ref or ref in seen:
            continue
        seen.add(ref)
        snippet = " … ".join((h.get("highlight") or {}).get(field, []))
        hits.append({"ref": ref, "version": src.get("version"), "lang": src.get("lang"),
                     "score": h.get("_score"), "snippet": strip_html(snippet)})
    return hits


def fetch_text(ref: str) -> dict:
    data = _request(f"/api/texts/{urllib.parse.quote(ref)}?context=0&commentary=0")
    return {"en": strip_html(data.get("text"))[:1200], "he": strip_html(data.get("he"))[:800]}


# --- optional semantic rerank ----------------------------------------------

_model = None


def rerank(query: str, items: list[dict]) -> list[dict]:
    """Score items by cosine similarity between query and their English text."""
    global _model
    from sentence_transformers import SentenceTransformer, util  # optional dep

    if _model is None:
        _model = SentenceTransformer("sentence-transformers/all-MiniLM-L6-v2")
    texts = [(i.get("text") or {}).get("en") or i.get("snippet") or i["ref"] for i in items]
    q = _model.encode(query, convert_to_tensor=True)
    docs = _model.encode(texts, convert_to_tensor=True)
    for item, score in zip(items, util.cos_sim(q, docs)[0].tolist()):
        item["similarity"] = round(score, 4)
    return sorted(items, key=lambda i: i["similarity"], reverse=True)


# --- orchestration ----------------------------------------------------------


def run(query: str, size: int = 20, with_text: bool = True, do_rerank: bool = False) -> dict:
    out: dict = {"query": query, "topics": [], "search": [], "errors": []}

    try:
        slugs = find_topics(query)
    except Exception as e:  # noqa: BLE001 - surface every failure, keep going
        slugs = []
        out["errors"].append(f"topic lookup: {e}")
    for t in slugs:
        try:
            out["topics"].append(topic_sources(t["slug"]))
        except Exception as e:  # noqa: BLE001
            out["errors"].append(f"topic {t['slug']}: {e}")

    try:
        out["search"] = full_text_search(query, size)
    except Exception as e:  # noqa: BLE001
        out["errors"].append(f"search: {e}")

    # one de-duplicated pool of refs, tagged with where each came from
    pool: dict[str, dict] = {}
    for t in out["topics"]:
        for r in t["refs"]:
            pool.setdefault(r["ref"], {"ref": r["ref"], "from": []})["from"].append(f"topic:{t['slug']}")
    for h in out["search"]:
        item = pool.setdefault(h["ref"], {"ref": h["ref"], "from": []})
        item["from"].append("search")
        item.setdefault("snippet", h["snippet"])

    if with_text and pool:
        def load(item):
            try:
                item["text"] = fetch_text(item["ref"])
            except Exception as e:  # noqa: BLE001
                item["text"] = {"en": "", "he": "", "error": str(e)}
        with ThreadPoolExecutor(max_workers=6) as ex:
            list(ex.map(load, pool.values()))

    combined = list(pool.values())
    if do_rerank and combined:
        try:
            combined = rerank(query, combined)
        except ImportError:
            out["errors"].append("rerank: pip install sentence-transformers")
    out["combined"] = combined
    return out


def print_report(res: dict) -> None:
    print(f"\n=== {res['query']} ===")
    for t in res["topics"]:
        print(f"\n[topic] {t['title']} {t['he_title']}  ({t['slug']}, {len(t['refs'])} refs)")
        if t["description"]:
            print(f"  {t['description'][:300]}")
    print(f"\n[full-text search] {len(res['search'])} hits")
    print(f"\n--- combined ({len(res['combined'])} sources) ---")
    for i, item in enumerate(res["combined"], 1):
        sim = f"  sim={item['similarity']:.3f}" if "similarity" in item else ""
        print(f"\n{i:>2}. {item['ref']}  [{', '.join(item['from'])}]{sim}")
        text = (item.get("text") or {}).get("en") or item.get("snippet") or ""
        if text:
            print(f"    {text[:280]}")
    for e in res["errors"]:
        print(f"\n! {e}", file=sys.stderr)


# --- tiny web UI ------------------------------------------------------------

PAGE = """<!doctype html><meta charset=utf-8><meta name=viewport content="width=device-width">
<title>Sefaria explorer</title>
<style>
body{font:15px/1.5 system-ui;max-width:860px;margin:0 auto;padding:16px;color:#222}
input{font-size:16px;padding:8px;width:60%}button{font-size:16px;padding:8px 14px}
.card{border:1px solid #ddd;border-radius:8px;padding:10px 14px;margin:10px 0}
.ref{font-weight:600}.tag{font-size:12px;background:#eef;border-radius:4px;padding:1px 6px;margin-left:4px}
.he{direction:rtl;font-family:serif;color:#444}.muted{color:#777;font-size:13px}
</style>
<h2>Sefaria explorer</h2>
<form id=f><input id=q placeholder="e.g. honoring parents" autofocus>
<label><input type=checkbox id=rr> rerank</label> <button>Search</button></form>
<div id=out></div>
<script>
const esc=s=>(s||"").replace(/[&<>]/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;"}[c]));
f.onsubmit=async e=>{e.preventDefault();out.innerHTML="<p class=muted>Searching…</p>";
 const r=await fetch("/api?q="+encodeURIComponent(q.value)+"&rerank="+(rr.checked?1:0));
 const d=await r.json();let h="";
 for(const t of d.topics)h+=`<div class=card><b>Topic: ${esc(t.title)}</b> <span class=he>${esc(t.he_title)}</span>
  <div class=muted>${esc(t.description)}</div><div class=muted>${t.refs.length} curated sources</div></div>`;
 h+=`<h3>${d.combined.length} sources</h3>`;
 for(const i of d.combined){const tx=i.text||{};
  h+=`<div class=card><a class=ref target=_blank href="https://www.sefaria.org/${encodeURIComponent(i.ref.replace(/ /g,"_"))}">${esc(i.ref)}</a>
  ${i.from.map(x=>`<span class=tag>${esc(x)}</span>`).join("")}${i.similarity!=null?`<span class=tag>sim ${i.similarity.toFixed(3)}</span>`:""}
  <div>${esc(tx.en||i.snippet)}</div><div class=he>${esc(tx.he)}</div></div>`}
 for(const e of d.errors)h+=`<p style=color:#b00>${esc(e)}</p>`;
 out.innerHTML=h};
</script>"""


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        url = urllib.parse.urlparse(self.path)
        if url.path == "/api":
            qs = urllib.parse.parse_qs(url.query)
            query = (qs.get("q") or [""])[0].strip()
            body = json.dumps(run(query, do_rerank=qs.get("rerank") == ["1"]) if query
                              else {"error": "empty query"}).encode()
            ctype = "application/json"
        else:
            body, ctype = PAGE.encode(), "text/html; charset=utf-8"
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("search", help="query from the command line")
    s.add_argument("query")
    s.add_argument("--size", type=int, default=20, help="full-text hits to fetch")
    s.add_argument("--no-text", action="store_true", help="skip fetching each source's text")
    s.add_argument("--rerank", action="store_true", help="semantic rerank (sentence-transformers)")
    s.add_argument("--json", action="store_true", help="print raw JSON")
    v = sub.add_parser("serve", help="local web UI")
    v.add_argument("--port", type=int, default=8765)
    args = p.parse_args(argv)

    if args.cmd == "search":
        res = run(args.query, args.size, not args.no_text, args.rerank)
        print(json.dumps(res, ensure_ascii=False, indent=2)) if args.json else print_report(res)
    else:
        print(f"Open http://localhost:{args.port}")
        ThreadingHTTPServer(("127.0.0.1", args.port), Handler).serve_forever()


if __name__ == "__main__":
    main()
