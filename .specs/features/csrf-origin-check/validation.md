# CSRF Origin Check Validation

**Verdict**: PASS

**Date**: 2026-11-22
**Spec**: `.specs/features/csrf-origin-check/spec.md`
**Diff range**: `cb1f1e9` (`cb1f1e9^..cb1f1e9`); middleware and `tests/test_csrf.py` unchanged through HEAD `346528d`
**Verifier**: independent sub-agent (author ≠ verifier)

---

## Task Completion

| Task | Status | Notes |
| ---- | ------ | ----- |
| Single change (Tasks phase skipped, Medium scope) | ✅ Done | `farmcortes/app.py:86-97` middleware `reject_cross_origin_writes` + `tests/test_csrf.py` |

---

## Spec-Anchored Acceptance Criteria

`BLOCKED = {"detail": "Origem não permitida"}` (`tests/test_csrf.py:6`). TestClient sends `Host: testserver`. Handler execution is observed via the patched `begin_discovery_run`, which records into `client.calls` (`tests/test_csrf.py:15`).

| Req | Criterion (WHEN X THEN Y) | Spec-defined outcome | `file:line` + assertion | Result |
| --- | ------------------------- | -------------------- | ----------------------- | ------ |
| CSRF-01 | POST/PUT/PATCH/DELETE with `Origin` netloc ≠ `Host` | 403, body `{"detail": "Origem não permitida"}`, handler not run | `tests/test_csrf.py:24` `response.status_code == 403`; `tests/test_csrf.py:25` `response.json() == BLOCKED`; `tests/test_csrf.py:26` `client.calls == []` (parametrized over all 4 methods, `:21`) | ✅ PASS |
| CSRF-01 (edge) | `Origin` differs from `Host` only by port | 403 + exact body | `tests/test_csrf.py:31` `response.status_code == 403`; `tests/test_csrf.py:32` `response.json() == BLOCKED`; `tests/test_csrf.py:33` `client.calls == []` | ✅ PASS |
| CSRF-02 | `Origin: null` on a write | 403 + exact body | `tests/test_csrf.py:38` `response.status_code == 403`; `tests/test_csrf.py:39` `response.json() == BLOCKED`; `tests/test_csrf.py:40` `client.calls == []` | ✅ PASS (POST only; middleware is method-agnostic within the write set, M2 shows method coverage is enforced by CSRF-01) |
| CSRF-03 | No `Origin` + `Sec-Fetch-Site: cross-site` | 403 + exact body | `tests/test_csrf.py:45` `response.status_code == 403`; `tests/test_csrf.py:46` `response.json() == BLOCKED`; `tests/test_csrf.py:47` `client.calls == []` | ✅ PASS |
| CSRF-04 | Check not applied to GET, HEAD, OPTIONS | not blocked (no 403) even with hostile `Origin` + `Sec-Fetch-Site: cross-site` | `tests/test_csrf.py:52` `client.get("/health", headers=headers).status_code == 200`; `tests/test_csrf.py:53` `client.head(...).status_code != 403`; `tests/test_csrf.py:54` `client.options(...).status_code != 403` | ✅ PASS (`!= 403` is the exact negation of the block outcome; spec defines no other status for HEAD/OPTIONS) |
| CSRF-05 | Write with `Origin` netloc == `Host` | passed to handler normally | `tests/test_csrf.py:59` `response.status_code == 409`; `tests/test_csrf.py:60` `response.json() == {"detail": "Já existe uma varredura em andamento"}`; `tests/test_csrf.py:61` `len(client.calls) == 1` | ✅ PASS |
| CSRF-06 | Write with no `Origin` and no `Sec-Fetch-Site` | passed to handler | `tests/test_csrf.py:67` `response.status_code == 409`; `tests/test_csrf.py:68` `len(client.calls) == 1` (param `{}`, `:64`) | ✅ PASS |
| CSRF-06 (edge) | No `Origin`, `Sec-Fetch-Site` = `same-origin` / `none` | passed to handler | `tests/test_csrf.py:67-68` same assertions (params `:64`) | ✅ PASS |

**Status**: ✅ All ACs covered, 8/8 criteria matched spec outcome, 0 spec-precision gaps.

---

## Discrimination Sensor

Scratch: full repo copy (no `.git`) at `scratchpad/verify-csrf/`; each mutant applied to `farmcortes/app.py` from a pristine backup, `tests/test_csrf.py` run, file restored. Security path → expanded depth (10 mutants).

| # | File:line | Description | Result | Killed? |
| - | --------- | ----------- | ------ | ------- |
| M1 | `farmcortes/app.py:94` | Missing `Origin` always allowed (ignore `Sec-Fetch-Site`) | 1 failed | ✅ Killed |
| M2 | `farmcortes/app.py:89` | DELETE added to exempt set | 1 failed | ✅ Killed |
| M3 | `farmcortes/app.py:92` | Compare hostname only, ignore port | 1 failed | ✅ Killed |
| M4 | `farmcortes/app.py:89` | Check applied to every method (GET too) | 1 failed | ✅ Killed |
| M5 | `farmcortes/app.py:91` | `Origin: null` treated as absent | 1 failed | ✅ Killed |
| M6 | `farmcortes/app.py:96` | Block status 403 → 400 | 7 failed | ✅ Killed |
| M7 | `farmcortes/app.py:96` | Handler runs (`call_next`) before returning 403 | 4 failed | ✅ Killed |
| M8 | `farmcortes/app.py:89` | HEAD removed from exempt set | 1 failed | ✅ Killed |
| M9 | `farmcortes/app.py:92` | Scheme-sensitive full-origin compare (`https://` + Host) | 1 failed | ✅ Killed |
| M10 | `farmcortes/app.py:92` | Substring match (`host in origin`) | 1 failed | ✅ Killed |

**Sensor depth**: expanded (security path, ≥5 manual mutants)
**Result**: 10/10 killed - PASS ✅
**Isolation**: real-tree `git status --porcelain` baseline was empty; after sensor it differs only by this `validation.md`. No `git stash` used.

---

## Code Quality

| Principle | Status |
| --------- | ------ |
| Minimum code (one 11-line middleware, stdlib `urlparse`) | ✅ |
| Surgical changes (imports + middleware only in `app.py`) | ✅ |
| No scope creep | ✅ |
| Matches patterns (FastAPI `{"detail": ...}` error shape) | ✅ |
| Spec-anchored outcome check (asserted values match spec) | ✅ |
| Per-layer coverage: block + pass-through + edge paths for the middleware | ✅ |
| Every test maps to a spec requirement (tests tagged `# CSRF-0N`) - no unclaimed tests | ✅ |
| Documented guidelines followed: none - strong defaults applied | ✅ |

---

## Edge Cases

- [x] `Origin` differs from `Host` only by port → 403 (`tests/test_csrf.py:29-33`)
- [x] `Sec-Fetch-Site` = `same-origin` / `none` without `Origin` → passes (`tests/test_csrf.py:64-68`)

Non-blocking observations (outside spec ACs, no fix task):
- Scheme is not compared (`https://host` passes against an http `Host`); this is the spec's chosen assumption (netloc vs Host).
- If a request carried no `Host` header, `Origin: null` (netloc `""`) would equal the default `""` and pass. Not reachable behind nginx (`Host $host`) or any HTTP/1.1 client.
- The story-2 Independent Test example (worker POST with Bearer → 202) is not literally exercised with the worker route; CSRF-06 is covered route-agnostically at `tests/test_csrf.py:64-68`, which is enough for a route-independent middleware.

---

## Gate Check

- **Gate command** (local Windows venv + fcntl shim; official gate is the user's Linux VPS pytest): `python -m pytest -q -p no:warnings tests/test_csrf.py`
- **Result**: 12 passed, 0 failed, 0 skipped
- **Test count before feature**: 0 in `tests/test_csrf.py`
- **Test count after feature**: 12 (8 functions, parametrized)
- **Delta**: +12
- **Full suite (informational)**: 119 passed, 6 failed. All 6 are environmental on Windows and do not touch the middleware (no 403/`Origem` in any failure): `test_pipeline.py::test_discovery_run_is_locked_and_records_status` (fcntl shim makes locks no-op), `test_social.py::TestExportPack::test_caption_duration_warning` (ffmpeg 120s timeout), `test_pipeline.py::test_youtube_upload_resumes_after_transient_transport_failure`, and `test_live.py` real-capture tests (flaky/timing). Confirm on the Linux VPS.

---

## Requirement Traceability Update

| Requirement | Previous Status | New Status |
| ----------- | --------------- | ---------- |
| CSRF-01 | Implementing | ✅ Verified |
| CSRF-02 | Implementing | ✅ Verified |
| CSRF-03 | Implementing | ✅ Verified |
| CSRF-04 | Implementing | ✅ Verified |
| CSRF-05 | Implementing | ✅ Verified |
| CSRF-06 | Implementing | ✅ Verified |

(Verifier did not edit `spec.md`; orchestrator applies the status update.)

---

## Summary

**Overall**: ✅ Ready

**Spec-anchored check**: 8/8 criteria (6 ACs + 2 edges) matched spec outcome, 0 spec-precision gaps
**Sensor**: 10/10 mutations killed
**Gate**: 12 passed, 0 failed (`tests/test_csrf.py`)

**What works**: cross-origin, port-mismatch, `null` origin and cross-site-without-Origin writes all return the exact 403 body without running the handler; reads are exempt; same-origin and Origin-less writes reach the handler.

**Issues found**: none.

**Next steps**: run the full suite on the Linux VPS to clear the 6 environmental failures.
