# Dead Code Cleanup - Verification Report

**Verdict**: PASS
**Date**: 2026-11-22
**Spec**: `.specs/features/dead-code-cleanup/spec.md`
**Diff range**: `964b445` + `a98bc53` + fix `774c677` (parent `a8e0870`..`774c677`; `346528d` in between is an unrelated worker change)
**Verifier**: independent sub-agent (author != verifier), re-verification iteration 2 of 3

Iteration 1 was FAIL (code correct, 4 surviving mutants M5, M10, M12, M13). Fix `774c677` is test-only (+24 lines in `tests/test_dead_code.py`, 4 new tests). All four mutants are now killed by behavior assertions.

---

## Task Completion

Tasks phase skipped (spec: "Tasks pulado"). Commits `964b445`, `a98bc53`, `774c677` present on `main`; no partial work.

---

## Spec-Anchored Acceptance Criteria

| Req | Criterion | Spec-defined outcome | `file:line` + assertion | Result |
| --- | --------- | -------------------- | ----------------------- | ------ |
| DEAD-01 | stubs absent | no `farmcortes/longcuts.py`, no `farmcortes/scan.py` | `tests/test_dead_code.py:7-8` - `assert not (ROOT / "farmcortes" / "longcuts.py").exists()` / `... "scan.py").exists()`. Confirmed with `ls` (both missing) | ✅ |
| DEAD-02 | no module refs in farmcortes/tests/deploy/docs | zero hits | `tests/test_dead_code.py:22` - `assert hits == []`. Independent `grep -rn "longcuts\|farmcortes.scan\|from .scan"` over the four folders (excluding the test itself): no hits | ✅ |
| DEAD-03 | single implementation used by `transcribe` and `LiveTranscriber.__call__` | both callers route through `pipeline.captions_from_segments` | `tests/test_dead_code.py:85-86` - `pipeline.transcribe(...) == expected` and `live.LiveTranscriber()(...) == expected` (expected drops the empty wordless segment the old live loop emitted); `:88` source-text count `== 1`. Code: `farmcortes/pipeline.py:207`, `farmcortes/live.py:127`, def at `farmcortes/pipeline.py:212` | ✅ |
| DEAD-04 | "Oi"/" tudo"/" bem." 0.0-0.4/0.4-0.8/0.8-1.2 | `[Caption(0.0, 1.2, "Oi tudo bem.")]`; sentence end closes the group | `tests/test_dead_code.py:49` - `== [Caption(0.0, 1.2, "Oi tudo bem.")]`; `:93-95` - `["Oi.", " tudo", " bem"]` gives `[Caption(0.0, 0.4, "Oi."), Caption(0.4, 1.2, "tudo bem")]` (punctuation rule independent of segment end) | ✅ |
| DEAD-05 | unpunctuated group reaching 3.3 s closes at that word | close when `end - group_start >= 3.3`, next word starts a new caption | `tests/test_dead_code.py:100-102` - word ending exactly at 3.3 gives `[Caption(0, 3.3, "a b c"), Caption(3.3, 3.5, "d")]`; `:107` - group at 3.25 stays open, `[Caption(0, 3.25, "a b c")]`; `:54-56` overshoot case. `3.3 - 0 >= 3.3` is float-exact | ✅ |
| DEAD-06 | wordless segment, text `" Olá "` | `Caption(segment.start, segment.end, "Olá")` | `tests/test_dead_code.py:60` - `== [Caption(5.0, 7.0, "Olá")]` | ✅ |
| DEAD-07 | wordless segment, empty text | no caption | `tests/test_dead_code.py:64` - `== []`; also `:78-86` via both batch and live paths | ✅ |
| DEAD-08 | glossary term in caption text | substitution applied to each caption | `tests/test_dead_code.py:68-70` - word path `== [Caption(0, 1, "Farmcortes!")]`; `:111-112` - wordless path `== [Caption(1, 2, "Farmcortes")]` | ✅ |
| DEAD-09 | empty segment list | `[]` | `tests/test_dead_code.py:74` - `captions_from_segments([]) == []` | ✅ |

Behavior preservation (checked in iteration 1, unchanged since `774c677` touches only tests): `captions_from_segments` (`farmcortes/pipeline.py:212-235`) is the former batch loop verbatim. Language-probability handling stays outside the function: batch raises `PipelineError` (`farmcortes/pipeline.py:205-206`), live returns `[]` (`farmcortes/live.py:125-126`). The only live change is the one the spec allows (empty wordless segment no longer emits an empty caption).

**Status**: ✅ 9/9 requirements matched spec outcome; no spec-precision gaps.

---

## Edge Cases

- [x] Empty segment list returns `[]` - `tests/test_dead_code.py:74`

---

## Discrimination Sensor

Scratch: full copy without `.git` at `scratchpad/verify-dead2/`. One mutation at a time via a script that asserts each target string occurs exactly once, runs `pytest -q tests/test_dead_code.py` in the copy, and restores the original text before the next mutant. Final content matched the real file (`diff --strip-trailing-cr`). Scratch deleted afterwards. No `git stash`.

| # | File:line | Mutation | Outcome (failing tests) |
| - | --------- | -------- | ----------------------- |
| M5 | `farmcortes/pipeline.py:234` | drop glossary on wordless-segment text | ✅ Killed (behavior): `test_glossary_is_applied_to_wordless_segment` |
| M10 | `farmcortes/pipeline.py:222` | sentence punctuation never closes a group | ✅ Killed (behavior): `test_punctuation_closes_group_mid_segment` |
| M12 | `farmcortes/pipeline.py:222` | threshold `3.3 + 0.2` (literal `>= 3.3` kept) | ✅ Killed (behavior): `test_group_closes_exactly_at_3_3_seconds` |
| M13 | `farmcortes/pipeline.py:222` | strict `> 3.3` (literal `>= 3.3` kept) | ✅ Killed (behavior): `test_group_closes_exactly_at_3_3_seconds` |
| M2 | `farmcortes/pipeline.py:222` | `>= 3.3` -> `> 3.3` | ✅ Killed (behavior): `test_group_closes_exactly_at_3_3_seconds`, plus source count in `test_batch_and_live_share_the_conversion` (was source-count-only in iteration 1) |
| M1 | `farmcortes/pipeline.py:222` | `3.3` -> `4.0` | ✅ Killed (behavior): `test_group_closes_at_3_3_seconds`, `test_group_closes_exactly_at_3_3_seconds`, `test_batch_and_live_share_the_conversion` |
| M14 | `farmcortes/pipeline.py:222` | threshold `3.3 - 0.3` (literal kept) | ✅ Killed (behavior): `test_group_closes_at_3_3_seconds`, `test_group_below_3_3_seconds_stays_open` |
| M4 | `farmcortes/pipeline.py:225` | drop glossary on closed word bucket | ✅ Killed (behavior): `test_glossary_is_applied` |
| M6 | `farmcortes/pipeline.py:233` | `elif segment.text.strip():` -> `else:` | ✅ Killed (behavior): `test_empty_segment_without_words_is_skipped`, `test_batch_and_live_share_the_conversion` (live path) |
| M8 | `farmcortes/pipeline.py:228` | drop trailing-bucket flush | ✅ Killed (behavior): 4 tests |

**Sensor depth**: lightweight+ (10 mutations: the 4 previous survivors + 6 previously killed)
**Result**: 10/10 killed, all by behavior assertions - PASS ✅

Isolation: real-tree `git status --porcelain` before the sensor = `?? .specs/features/dead-code-cleanup/validation.md`; identical after cleanup.

---

## Code Quality

| Principle | Status |
| --------- | ------ |
| Minimum code | ✅ pure extraction, one shared function |
| Surgical changes | ✅ fix commit is test-only |
| No scope creep | ✅ livecut/titles/model loading untouched |
| Matches patterns | ✅ |
| Spec-anchored outcome check | ✅ all asserted values match spec outcomes |
| Every test maps to a spec requirement | ✅ each test tagged DEAD-0x |
| Documented guidelines followed | none found - strong defaults applied |

Minor (non-blocking): `tests/test_dead_code.py:88` still asserts on source text (`">= 3.3"` count); now redundant with the behavior boundary tests. Imports mid-file at `tests/test_dead_code.py:25-31` (style only).

---

## Gate Check

- **Gate command**: `PYTHONPATH=<shim> <venv>/python -m pytest -q -p no:warnings tests/test_dead_code.py` (local Windows; official gate is pytest on the Linux VPS)
- **Feature tests**: 13 passed, 0 failed, 0 skipped
- **Test count delta**: +13 vs. pre-feature (9 in iteration 1 + 4 in `774c677`), none removed
- **Full suite**: not re-run this iteration; `774c677` changes only `tests/test_dead_code.py`. Iteration 1 full suite: 120 passed, 5 environment-only failures unrelated to caption code

---

## Requirement Traceability Update

| Requirement | New Status |
| ----------- | ---------- |
| DEAD-01 .. DEAD-09 | ✅ Verified |

---

## Summary

**Overall**: ✅ Ready

**Spec-anchored check**: 9/9 ACs matched spec outcome, 0 spec-precision gaps
**Sensor**: 10/10 killed (all behavior-level)
**Gate**: 13 passed, 0 failed (feature tests)

**What works**: stubs removed with no refs; one conversion function used by batch and live; behavior preserved; boundary, punctuation and glossary rules now discriminated.

**Next steps**: none required. Optional cleanup: drop the source-text assertion at `tests/test_dead_code.py:88`.
