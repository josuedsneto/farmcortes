# Publish Gate Cleanup Validation

**Verdict**: PASS

**Date**: 2026-11-22
**Spec**: `.specs/features/publish-gate-cleanup/spec.md`
**Diff range**: `cb1f1e9..a8e0870` - commits `7c67777`, `5694677` (feature part only: `farmcortes/original.py`, `farmcortes/compilations.py`, `farmcortes/source_compilations.py`; the `.gitignore` line is unrelated), `88ac6a7`, `a8e0870`. Later commits up to HEAD `346528d` checked: none touches `_finish`, the CLIs, deploy units or README publishing text.
**Verifier**: independent sub-agent (author ≠ verifier)

---

## Task Completion

No `tasks.md` (spec allowed skipping it at ≤5 steps). All 10 requirements have code + tests in the diff range.

---

## Spec-Anchored Acceptance Criteria

| Req | Criterion | Spec-defined outcome | `file:line` + assertion | Result |
| --- | --------- | -------------------- | ----------------------- | ------ |
| PUB-01 | WHEN a clip job finishes rendering | `status="done"`, `progress=100`, `stage="Cortes prontos para revisão"`, `publication={}` | `tests/test_publish_gate.py:32` - `assert _final_state(database.get("job-1")) == FINAL` with `FINAL` at `tests/test_publish_gate.py:7` = `{"status": "done", "progress": 100, "stage": "Cortes prontos para revisão", "publication": {}}` (exact dict equality) | ✅ PASS |
| PUB-02 | WHILE `FARMCORTES_YOUTUBE_ENABLED=true`, finish without `publish_file` / YouTube API | no `publish_file` call; job ends in PUB-01 state | `tests/test_publish_gate.py:12-13` - env set to `true`, `youtube.publish_file` replaced by `pytest.fail(...)`; `tests/test_publish_gate.py:32` - final state `== FINAL`. Static: `farmcortes/pipeline.py` no longer imports `farmcortes.youtube` (grep: only URL helpers mention "youtube") | ✅ PASS |
| PUB-03 | WHEN job recovered from already-rendered clips | same final state as PUB-01 | `tests/test_publish_gate.py:50` - `assert _final_state(database.get("job-2")) == FINAL`; `:51` outputs `== ["corte-01.mp4"]` | ✅ PASS |
| PUB-04 | exactly one `publish_file` call site, in `review.publish_approved` | count == 1, file `review.py` | `tests/test_publish_gate.py:69` - `assert len(calls) == 1`; `:70` - `assert calls[0][0] == "farmcortes/review.py"`. Independent grep: only `farmcortes/review.py:77` (inside `publish_approved`, def at `farmcortes/review.py:45`) + the def at `farmcortes/youtube.py:233` | ✅ PASS |
| PUB-05 | no `farmcortes/backfill.py`, no `youtube.publish` | module absent; attribute absent | `tests/test_publish_gate.py:74` - `assert not (ROOT / "farmcortes" / "backfill.py").exists()`; `tests/test_publish_gate.py:55` - `assert not hasattr(youtube, "publish")`. Grep: no `backfill` or `publish_youtube` references left in tracked code | ✅ PASS |
| PUB-06 | `original` / `compilations` / `source_compilations` with `--publish` | exit code 2 (unknown argument) | `tests/test_publish_gate.py:85` - `assert result.returncode == 2`; `:86` - `assert "--publish" in result.stderr`; parametrized over all 3 modules (`:77`) | ✅ PASS |
| PUB-07 | WHEN `original.generate` ends | manifest `status="rendered"`, `publication={}` | `tests/test_publish_gate.py:106` - `assert manifest["status"] == "rendered"`; `:107` - `assert manifest["publication"] == {}` (plus return value `:103-104`) | ✅ PASS |
| PUB-08 | no `--publish` in any unit in `deploy/` or `deploy/producao-atual/` | zero units with `--publish` | `tests/test_publish_gate.py:112` - `assert units` (non-vacuous; rglob covers both trees); `:113` - list of offending units `== []`. Grep over `deploy/`: no hits | ✅ PASS |
| PUB-09 | README with no auto-publish / `--publish`, plus review-only section | phrases absent; section naming `review.publish_approved` / Telegram | `tests/test_publish_gate.py:141` - `"--publish" not in readme`; `:142` - `"agendados nos próximos horários" not in readme`; `:143` - section sentence present; `:144` - `"review.publish_approved" in readme`. README section also names Telegram | ✅ PASS |
| PUB-10 | Edge: `compilations.run_once` with an old manifest that has `publication` | manifest left intact, no publish attempt | `tests/test_publish_gate.py:135` - `assert results == [old]`; `:136` - file content `== old`; `publish_file` stubbed to fail via `youtube_enabled` fixture | ✅ PASS |

**Status**: ✅ All 10 ACs covered with exact spec outcomes. No spec-precision gaps.

---

## Discrimination Sensor

Scratch: copy of the repo (no `.git`) at `<scratchpad>/verify-publish/`; each mutant applied alone, `tests/test_publish_gate.py` run, files restored from a pristine backup between mutants.

| # | File:line (scratch) | Mutation | Killed by | Killed? |
| - | ------------------- | -------- | --------- | ------- |
| M1 | `farmcortes/pipeline.py:520` | final stage `"Cortes prontos para revisão"` → `"Cortes prontos revisão"` | `test_finished_job...`, `test_recovered_job...` | ✅ Killed |
| M2 | `farmcortes/pipeline.py:520` | `progress=100` → `progress=98` | same 2 tests | ✅ Killed |
| M3 | `farmcortes/pipeline.py:518` | re-add `youtube.publish_file(...)` call in `_finish` | 2 pipeline tests + `test_review_queue_is_the_only_publish_file_caller` | ✅ Killed |
| M4 | `farmcortes/pipeline.py:521` | `publication={}` → `{"platform": "youtube", "status": "failed"}` | 2 pipeline tests | ✅ Killed |
| M5 | `farmcortes/pipeline.py:548` | recovery path bypasses `_finish` (old `"Cortes prontos"`, progress 98) | `test_recovered_job_gets_the_same_final_state` | ✅ Killed |
| M6 | `farmcortes/original.py:214` | re-add `--publish` argparse flag | `test_publish_flag_is_rejected[original]` | ✅ Killed |
| M7 | `farmcortes/compilations.py:265` | `run_once` rewrites manifest with `publication={}` | `test_old_compilation_manifest_is_left_untouched` | ✅ Killed |
| M8 | `farmcortes/original.py:204` | manifest `status` `"rendered"` → `"published"` | `test_original_short_manifest_is_rendered_only` | ✅ Killed |
| M9 | `deploy/producao-atual/systemd/farmcortes-compilations.service:15` | re-add `--publish` to `ExecStart` | `test_deploy_units_do_not_pass_publish` | ✅ Killed |
| M10 | `farmcortes/source_compilations.py:195` | re-add `--publish` argparse flag | `test_publish_flag_is_rejected[source_compilations]` | ✅ Killed |

**Sensor depth**: expanded (10 manual mutants; feature is the publishing-policy gate)
**Result**: 10/10 killed - PASS ✅
**Isolation**: real tree `git status --porcelain` was empty before the sensor. After: only this `validation.md` plus `?? .specs/features/dead-code-cleanup/validation.md`, which a parallel session created during this run (not written by the Verifier, outside this feature).

---

## Code Quality

| Principle | Status |
| --------- | ------ |
| Minimum code (`_finish` is one `update`; dead slot helpers removed) | ✅ |
| Surgical changes | ✅ (`.gitignore` line in `5694677` is unrelated, from the parallel session) |
| No scope creep (review.py, telegram.py, `publish_file`, schema untouched) | ✅ |
| Matches patterns | ✅ |
| Spec-anchored outcome check (asserted values match spec) | ✅ |
| Per-layer coverage (1:1 AC mapping, every test tagged with a PUB id) | ✅ |
| Every test maps to a spec requirement - no unclaimed tests | ✅ |
| Documented guidelines followed: none - strong defaults applied | ✅ |

---

## Edge Cases

- [x] Old compilation manifest with `publication` is left intact and not published (PUB-10, `tests/test_publish_gate.py:135-136`).

---

## Gate Check

- **Gate command**: `pytest -q -p no:warnings tests/test_publish_gate.py tests/test_review.py tests/test_pipeline.py` (local Windows venv + shims; official gate is the user's Linux VPS run)
- **Result**: 54 passed, 2 failed, 0 skipped
- **Failures**: `test_pipeline.py::test_discovery_run_is_locked_and_records_status`, `test_pipeline.py::test_youtube_upload_resumes_after_transient_transport_failure` - both known environment-only failures (no `fcntl` / `googleapiclient` on Windows), also failing on baseline `cf5467f`.
- **Full suite (informative)**: 120 passed, 5 failed - the 2 above, 2 known `test_live.py` process tests, and `test_social.py::TestExportPack::test_caption_duration_warning` (needs an `ffmpeg` binary; `farmcortes/social/` and `tests/test_social.py` unchanged since `cf5467f`, not in this diff).
- **Test integrity**: `tests/test_review.py` and `tests/test_pipeline.py` unchanged in `cb1f1e9..HEAD`; `tests/test_publish_gate.py` adds 12 test cases. Nothing deleted or weakened.

---

## Observations (non-blocking)

1. `compilations.metadata_for_compilation`, `source_compilations.metadata`, `youtube.schedule_slots` and `youtube.ranked_outputs` now have no production callers (only tests). The spec deliberately kept `schedule_slots` / `metadata_for`; can be removed with the future review-queue feature or kept for it.
2. The PUB-04 test matches `publish_file(` per line; an aliased import (`publish_file as x`) would slip past it. Fine at today's size; the grep matches the spec's Independent Test.
3. Unrelated `.gitignore` line `".env" ` from `5694677` matches a file literally named `".env"`, not `.env`. Outside this feature.
4. Spec traceability still says `Implementing`; the Verifier only writes `validation.md`. Orchestrator should mark PUB-01..10 `Verified`.

---

## Requirement Traceability Update

| Requirement | Previous Status | New Status |
| ----------- | --------------- | ---------- |
| PUB-01..PUB-10 | Implementing | ✅ Verified |

---

## Summary

**Overall**: ✅ Ready

**Spec-anchored check**: 10/10 ACs matched spec outcome, 0 spec-precision gaps
**Sensor**: 10/10 mutations killed
**Gate**: 54 passed, 2 known environment-only failures
