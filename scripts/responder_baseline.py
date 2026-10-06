"""Replay a fixed set of page comments against the live responder and record latency.

Posts each question as a new agent-owned comment on a page, waits for the agent's
reply, sends one follow-up in the same thread, waits again, then resolves the
thread. Client-side times are joined with the responder run records in the store
(run kind `responder`, started inside the wait window) for model-side timings,
tokens and escalations. Rerun the same set after a responder change to compare.

    uv run python scripts/responder_baseline.py --out results.json [--limit N]
"""

import argparse
import json
import os
import sqlite3
import time
import urllib.request
import uuid
from datetime import datetime, timezone
from pathlib import Path

DECK = "http://localhost:8787"
PAGE = "p-1dc970c7/fleet-today"
SELECTOR = {"type": "FragmentSelector", "value": "live-pages"}
THREADS = [
    ("What is this page for?", "Who can comment on it?"),
    ("What does M4 wait on?", "Is any of that already done?"),
    ("What shipped most recently?", "Which of those changes touched page comments?"),
    ("Which decisions are parked?", "Why are they parked rather than dropped?"),
    ("How do agents write pages?", "Can an agent edit a page someone is reading?"),
    ("Do pages update live?", "What happens when the deck restarts?"),
    ("Summarise this section in one sentence.", "Shorter, please."),
    ("What are the recent runs on this page?", "Did any of them fail?"),
    ("Where do comments end up after I post them?", "Can I see them from the CLI?"),
    ("Is this page a prototype or finished?", "What would make it finished?"),
]
TIMEOUT_S = 180


def call(path, body=None):
    request = urllib.request.Request(DECK + path, method="POST" if body is not None else "GET",
                                     data=None if body is None else json.dumps(body).encode(),
                                     headers={"Content-Type": "application/json", "Origin": DECK,
                                              "Sec-Fetch-Site": "same-origin"})
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.loads(response.read())


def thread(item):
    return next(t for t in call(f"/api/pages/{PAGE}")["threads"] if t["id"] == item)


def wait_for_agent(item, replies_before):
    deadline = time.monotonic() + TIMEOUT_S
    while time.monotonic() < deadline:
        replies = thread(item)["replies"]
        agent = [r for r in replies[replies_before:] if r["actor"] != "user"]
        if agent:
            return time.time(), len(replies)
        time.sleep(0.25)
    return None, None


def interaction(item, kind, text, sent, replies_before):
    received, count = wait_for_agent(item, replies_before)
    return {"item": item, "kind": kind, "text": text, "sent": sent, "received": received,
            "client_total_s": None if received is None else received - sent}, count


def run(limit):
    page = call(f"/api/pages/{PAGE}")
    results = []
    for question, follow_up in THREADS[:limit]:
        sent = time.time()
        item = call(f"/api/pages/{PAGE}/comment-text", {
            "revision": page["revision"], "comment_id": str(uuid.uuid4()), "body": question,
            "selector": SELECTOR, "owner": "agent"})["id"]
        first, count = interaction(item, "new thread", question, sent, 0)
        results.append(first)
        if count is not None:
            sent = time.time()
            call(f"/api/pages/{PAGE}/reply", {"item_id": item, "body": follow_up})
            results.append(interaction(item, "follow-up", follow_up, sent, count + 1)[0])
        call(f"/api/pages/{PAGE}/resolve", {"item_id": item})
        print(json.dumps({k: results[-1][k] for k in ("kind", "client_total_s")}), flush=True)
    return results


def join_runs(results, store):
    connection = sqlite3.connect(store)
    runs = [json.loads(row[0]) for row in connection.execute(
        "SELECT record FROM execution_run WHERE json_extract(record, '$.kind') = 'responder'")]
    for result in results:
        window = (result["sent"], result["received"] or result["sent"] + TIMEOUT_S)
        matches = [run for run in runs
                   if window[0] <= datetime.fromisoformat(run["start"]).timestamp() <= window[1]]
        run = matches[0] if matches else None
        result["run"] = None if run is None else run["id"]
        if run is not None:
            timings = run.get("timings") or {}
            usage = (run.get("usage") or {}).get("reports", [{}])[0]
            start = datetime.fromisoformat(run["start"]).timestamp()
            result.update(submit_to_request_s=start - result["sent"], first_text_s=timings.get("first_delta_s"),
                          responder_total_s=timings.get("total_s"), escalated=bool(run.get("reason")),
                          cached_input_tokens=usage.get("cachedInputTokens"), input_tokens=usage.get("inputTokens"),
                          output_tokens=usage.get("outputTokens"), warm=bool(usage.get("cachedInputTokens")))
    return results


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--limit", type=int, default=len(THREADS))
    parser.add_argument("--store", default=os.environ.get("FLEET_STORE",
                                                          str(Path.home() / ".config/fleet/fleet.db")))
    arguments = parser.parse_args()
    results = join_runs(run(arguments.limit), arguments.store)
    arguments.out.write_text(json.dumps({"page": PAGE, "recorded": datetime.now(timezone.utc).isoformat(),
                                         "interactions": results}, indent=2))


if __name__ == "__main__":
    main()
