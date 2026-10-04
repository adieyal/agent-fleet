# Known failures

| Test id | Failing since | Work item | Note |
| --- | --- | --- | --- |
| `tests/test_deck_browser.py::test_panel_breadcrumb_names_the_linked_work_and_opens_it[chromium]` (`tests/test_deck_browser.py:511`) | Verified on `4641086`, 2026-10-05 | `20956ae8-2762-4887-8297-62cbfab21b22` (extraction evidence) | After Escape, route remains `bench`; expected `room`. |
| `tests/test_deck_browser.py::test_each_step_names_the_work_it_serves_and_opens_it[chromium]` (`tests/test_deck_browser.py:617`) | Verified on `4641086`, 2026-10-05 | `20956ae8-2762-4887-8297-62cbfab21b22` (extraction evidence) | After Escape, route remains `bench`; expected `room`. |
| `tests/test_deck_browser.py::test_lone_milestone_steps_back_to_the_floor[chromium]` (`tests/test_deck_browser.py:640`) | Verified on `4641086`, 2026-10-05 | `20956ae8-2762-4887-8297-62cbfab21b22` (extraction evidence) | After Escape, route remains `bench`; expected `floor`. |
| `tests/test_deck_browser.py::test_p8_help_keeps_unsaved_guidance[chromium]` (`tests/test_deck_browser.py:2674`) | Verified on `4641086`, 2026-10-05 | `20956ae8-2762-4887-8297-62cbfab21b22` (extraction evidence) | Help names `Project library`; expected `Guidance editor`. |
| `tests/test_deck_browser.py::test_p2_history_archive_and_offline[chromium-desktop]` (`tests/test_deck_browser.py:2817`) | Verified on `4641086`, 2026-10-05 | `20956ae8-2762-4887-8297-62cbfab21b22` (extraction evidence) | History report tiles count is 0; expected 2. |
| `tests/test_deck_browser.py::test_p3_work_entrypoint_reads_real_audit[chromium]` (`tests/test_deck_browser.py:3001`) | Verified on `4641086`, 2026-10-05 | `20956ae8-2762-4887-8297-62cbfab21b22` (extraction evidence) | Clicking the first epic card times out. |
| `tests/test_deck_browser.py::test_triage_policy_room[chromium-desktop]` (`tests/test_deck_browser.py:3025`) | Verified on `4641086`, 2026-10-05 | `20956ae8-2762-4887-8297-62cbfab21b22` (extraction evidence) | Clicking the first epic card times out. |
| `tests/test_deck_browser.py::test_a2_batch4_world_hit_consequences[chromium-desktop]` (`tests/test_deck_browser.py:3341`) | Verified on `4641086`, 2026-10-05 | `20956ae8-2762-4887-8297-62cbfab21b22` (extraction evidence) | Project selection times out in module order; passes in isolation on base and extraction branch. |
| `tests/test_deck_browser.py::test_a2_batch4_history_status_and_http_prefixes[chromium-desktop]` (`tests/test_deck_browser.py:3425`) | Verified on `4641086`, 2026-10-05 | `20956ae8-2762-4887-8297-62cbfab21b22` (extraction evidence) | History does not show `queued (not started)` in module order; passes in isolation on both branches. |
| `tests/test_live_documents_browser.py::test_a_departed_jobs_report_opens_from_the_library[chromium]` (`tests/test_live_documents_browser.py:194`) | Verified on `4641086`, 2026-10-05 | `20956ae8-2762-4887-8297-62cbfab21b22` (extraction evidence) | Reader metadata lacks the expected space before `claude`: `worker · 0ld5ob ·claude…`. |

These browser failures reproduce on the extraction base `4641086`; they are recorded without changing presentation behavior or assertions. The deck tests reuse one page per viewport (`tests/test_deck_browser.py:75–127`), so isolation and module-order results differ for the two batch-4 cases. For example:

```python
# tests/test_deck_browser.py:617
expect(route).to_have_attribute('data-level', 'room')
# Base run reports: Actual value: bench
```

Validation used a throwaway `git archive 4641086` checkout, temporary Fleet paths from the test fixtures, and the existing virtual environment:

```text
# cwd: /tmp/fleet-extract-step3-browser-base
TMPDIR=/tmp /home/adi/Development/agent-fleet-extract/.venv/bin/python -m pytest -q -m browser tests/test_deck_browser.py --basetemp=/dev/shm/fleet-extract-step3-browser-base-deck
10 failed, 115 passed, 1 skipped in 334.59s (0:05:34)

# The two batch-4 cases, selected explicitly on both base and extraction branch:
1 failed, 2 passed in 48.24s (base: two batch-4 cases plus departed-report test)
2 passed in 29.41s (extraction branch: the two cases only)
```

The base module run also intermittently failed `test_a_tasks_title_opens_its_latest_report_and_its_documents_list_by_icon[chromium]` at `tests/test_deck_browser.py:672` because `bounding_box()` returned None. That case passed in the extraction branch's full browser run. Exact commands, failure output and the full extraction results are collected in the job outbox `M1.md` and `STEP3-BROWSER*.log`.
