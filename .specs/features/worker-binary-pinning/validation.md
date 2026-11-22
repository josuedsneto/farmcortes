# Worker Binary Pinning - Verification Report

**Verdict**: PASS
**Date**: 2026-11-22
**Spec**: `.specs/features/worker-binary-pinning/spec.md`
**Diff range**: `a98bc53..3568679` - code `346528d` (`farmcortes/worker.ps1`, `tests/test_worker.py`, spec status) + test fix `3568679` (`tests/test_worker.py`; also `.specs/LESSONS.md`, `.specs/lessons.json`)
**Verifier**: independent sub-agent (author != verifier), re-verification iteration 2 of 3

Iteration 1 FAILed because 4 of 9 mutants (M3, M5, M6, M7) survived the static tests. Commit `3568679` pins the full `Assert-Sha256` body and the exact `return` lines of `Test-YtDlp`/`Test-Deno`. All 4 former survivors are now killed, along with all 5 previously killed mutants and 2 new ones (11/11). `farmcortes/worker.ps1` has not changed since `346528d` (`git diff 346528d HEAD -- farmcortes/worker.ps1` is empty, and the file is byte-identical to the copy the iteration-1 behavioral harness used). So the 13/13 behavioral results from iteration 1 still apply.

---

## Task Completion

| Task | Status | Notes |
| ---- | ------ | ----- |
| Single-file change (Tasks phase skipped per spec "Coverage") | ✅ Done | `346528d` |
| Fix 1 from iteration 1 (pin decision logic in tests) | ✅ Done | `3568679`, test-only |

---

## Independent checks of pinned values (iteration 1, still valid, constants unchanged)

| Item | Script constant | Official source | Match |
| ---- | --------------- | --------------- | ----- |
| yt-dlp.exe 2026.08.19 SHA-256 | `farmcortes/worker.ps1:14` | `releases/download/2026.08.19/SHA2-256SUMS` | ✅ |
| deno-x86_64-pc-windows-msvc.zip v2.9.8 SHA-256 | `farmcortes/worker.ps1:17` | `.zip.sha256sum` (uppercase; `-ne` is case-insensitive) | ✅ |
| yt-dlp version vs `requirements.txt` (`yt-dlp==2026.8.19`) | `farmcortes/worker.ps1:12` `2026.08.19` | numerically equal | ✅ |
| Deno v2.9.8 age | `farmcortes/worker.ps1:15` | published 2026-10-21 (>= 14 days) | ✅ |
| `latest` in worker.ps1 | - | no match (also asserted at `tests/test_worker.py:55`) | ✅ |

---

## Spec-Anchored Acceptance Criteria

Behavioral evidence comes from the harness `scratchpad/vh/scenarios.ps1`, run by the iteration-1 verifier on the identical `worker.ps1` in Windows PowerShell 5.1, with fresh processes, scratch roots, `file:///` and partial-HTTP URLs, and fake binaries.

| Req | Criterion | Spec-defined outcome | Test `file:line` + assertion | Behavioral | Result |
| --- | --------- | -------------------- | ---------------------------- | ---------- | ------ |
| BIN-01 | Fixed URLs, no `latest` | yt-dlp `.../releases/download/2026.08.19/yt-dlp.exe`; Deno `.../releases/download/v<ver>/` | `tests/test_worker.py:55` `"latest" not in WORKER`; `:58` version == requirements; `:59` `_value("YtDlpUrl") == "https://github.com/yt-dlp/yt-dlp/releases/download/$YtDlpVersion/yt-dlp.exe"`; `:60` DenoUrl exact; `:61` curl uses these vars | S1 | ✅ PASS |
| BIN-02 | SHA-256 computed and compared to constant before run/extract | `Get-FileHash SHA256` vs fixed constant; mismatch aborts | `tests/test_worker.py:65-66` constants == official hashes; `:67` `ASSERT_SHA256 in WORKER` (full guard body `:71-76` incl. `.Hash -ne $Expected`); ordering `:81`, `:87` | S1 ok; S2/S3 rejected before run/extract | ✅ PASS (M5, M11 killed) |
| BIN-03 | Bad yt-dlp hash | delete file, throw `"Hash do yt-dlp nao confere"`, not executed | `tests/test_worker.py:80-81` `Assert-Sha256 $temporary $YtDlpSha256 "Hash do yt-dlp nao confere"` index < `Move-Item` index; guard body (`Remove-Item -Force $Path`, `throw $Message`) `:71-76` asserted at `:67`, `:82` | S2, S8: message exact, dir empty | ✅ PASS (M1, M7, M10 killed) |
| BIN-04 | Bad Deno zip hash | delete zip, throw `"Hash do Deno nao confere"`, not extracted | `tests/test_worker.py:86-87` exact message call index < `Expand-Archive` index; same guard `:67` | S3: message exact, no zip/extract dir | ✅ PASS (M8 killed) |
| BIN-05 | Installed yt-dlp reporting pinned version is reused | no download | `tests/test_worker.py:92` exact line `return $LASTEXITCODE -eq 0 -and "$version".Trim() -eq $YtDlpVersion` | S4, S5 kept | ✅ PASS (M3 killed) |
| BIN-06 | Other version / not starting | delete, verified download | `tests/test_worker.py:92` (same); `:94` `if (-not (Test-YtDlp)) {\n    Remove-Item -Force -ErrorAction SilentlyContinue $YtDlp` | S6 (`2025.01.01` replaced), S7 (broken replaced) | ✅ PASS (M3, M9 killed) |
| BIN-07 | Deno not reporting pinned version | delete, verified download | `tests/test_worker.py:93` exact line `return $LASTEXITCODE -eq 0 -and "$version" -match "^deno $([regex]::Escape($DenoVersion)) "`; `:95` `if (-not (Test-Deno))` + Remove-Item | S9 replaced, S10 kept | ✅ PASS (M6 killed) |
| BIN-08 | curl != 0 | delete partial file, throw `"Falha ao baixar o ... oficial"` | `tests/test_worker.py:100` `Remove-Item ... $temporary\n        throw "Falha ao baixar o yt-dlp oficial"`; `:101` same for `$archive` / Deno | S11, S12 (curl 18 partial), S13 | ✅ PASS (M4 killed) |

**Status**: ✅ All 8 ACs covered, and the assertions target the spec-defined values (exact URLs, hashes, messages, operators, ordering).

Spec-precision notes (minor, not blocking, carried from iteration 1):
- When the extracted `deno.exe` is missing or under 10 MB, `farmcortes/worker.ps1:82-84` throws without cleaning up. This behavior predates the feature, is outside BIN-01..08, and the next run cleans up at `:70-72`.
- An installed binary is trusted on `--version` alone (accepted in spec Assumptions, "Binário já instalado").

Residual note: the tests are textual. They pin the exact source of the guard and the version gates, and the behavior is evidenced by the iteration-1 harness, which the CI/VPS cannot run (no PowerShell). This is acceptable under the spec's Success Criteria ("teste estático" + manual Windows test).

---

## Discrimination Sensor

Scratch: repo copied without `.git` to `scratchpad/verify-worker2/`. Each mutation was applied by exact unique-string replacement to the copy's `farmcortes/worker.ps1`, then the gate was run on the copy, then the original was restored and byte-compared before the next mutation. The scratch was deleted afterwards.

| Mutation | File:line | Description | Result |
| -------- | --------- | ----------- | ------ |
| M3 | `farmcortes/worker.ps1:25` | Test-YtDlp `-and` -> `-or` | ✅ Killed (1 failed) - previously survived |
| M5 | `farmcortes/worker.ps1:42` | hash comparison `-ne` -> `-eq` | ✅ Killed (2 failed) - previously survived |
| M6 | `farmcortes/worker.ps1:35` | Test-Deno `-and` -> `-or` | ✅ Killed (1 failed) - previously survived |
| M7 | `farmcortes/worker.ps1:44` | `throw $Message` removed | ✅ Killed (2 failed) - previously survived |
| M1 | `farmcortes/worker.ps1:58-59` | yt-dlp hash check moved after `Move-Item` | ✅ Killed (1 failed) |
| M2 | `farmcortes/worker.ps1:13` | URL -> `releases/latest/download` | ✅ Killed (1 failed) |
| M4 | `farmcortes/worker.ps1:55` | partial-file cleanup removed on yt-dlp curl failure | ✅ Killed (1 failed) |
| M8 | `farmcortes/worker.ps1:79-80` | Deno hash check moved after `Expand-Archive` | ✅ Killed (1 failed) |
| M9 | `farmcortes/worker.ps1:48` | `Test-YtDlp` gate -> `Test-Path $YtDlp` | ✅ Killed (1 failed) |
| M10 (new) | `farmcortes/worker.ps1:43` | `Remove-Item -Force $Path` removed from guard | ✅ Killed (2 failed) |
| M11 (new) | `farmcortes/worker.ps1:42` | compare against `$YtDlpSha256` instead of `$Expected` (Deno zip checked against wrong hash) | ✅ Killed (2 failed) |

**Sensor depth**: expanded (security / supply-chain path), 11 manual mutations
**Result**: 11/11 killed - PASS ✅

Isolation: real-tree `git status --porcelain` before the sensor = `?? .specs/features/worker-binary-pinning/validation.md` (previous report). After cleanup it is the same, and that file is the only one written in the real tree. `git stash` was not used.

---

## Code Quality

| Principle | Status |
| --------- | ------ |
| Minimum code (one 6-line hash helper reused for both binaries) | ✅ |
| Surgical changes (`3568679` touches only `tests/test_worker.py` + lessons files) | ✅ |
| No scope creep | ✅ |
| Matches patterns (ASCII messages, existing curl flags, CRLF-normalized text asserts like the existing BIN-08 test) | ✅ |
| Spec-anchored outcome check (asserted values match spec) | ✅ |
| Per-layer Coverage Expectation met (decision operators, throw, delete, ordering all pinned) | ✅ |
| Every test maps to a spec requirement (each tagged BIN-xx) | ✅ |
| Documented guidelines followed: none for PowerShell, so strong defaults were applied | ✅ |

---

## Edge Cases

- [x] Download failure (curl != 0) removes the partial file and throws the existing message: `tests/test_worker.py:100-101`; behavioral S11, S12, S13.

---

## Gate Check

- **Gate command**: `PYTHONPATH=<scratchpad>/shim <scratchpad>/venv/Scripts/python -m pytest -q -p no:warnings tests/test_worker.py` (no tasks.md for this single-file feature)
- **Result**: 9 passed, 0 failed, 0 skipped (exit 0)
- **Test count before feature**: 3. **After**: 9. **Delta**: +6. No tests were removed. `3568679` replaced substring asserts with stricter full-text asserts (strengthened, not weakened).
- **Behavioral**: 13/13 scenarios OK (iteration 1 run on byte-identical `worker.ps1`)

---

## Requirement Traceability Update

| Requirement | Previous Status | New Status |
| ----------- | --------------- | ---------- |
| BIN-01 | Implementing | ✅ Verified |
| BIN-02 | ❌ Needs Fix | ✅ Verified |
| BIN-03 | ❌ Needs Fix | ✅ Verified |
| BIN-04 | ❌ Needs Fix | ✅ Verified |
| BIN-05 | ❌ Needs Fix | ✅ Verified |
| BIN-06 | ❌ Needs Fix | ✅ Verified |
| BIN-07 | ❌ Needs Fix | ✅ Verified |
| BIN-08 | Implementing | ✅ Verified |

(The verifier did not edit spec.md. The orchestrator updates the statuses.)

---

## Summary

**Overall**: ✅ Ready

**Spec-anchored check**: 8/8 ACs matched the spec outcome. No blocking spec-precision gaps (2 minor notes).
**Sensor**: 11/11 mutations killed (including former survivors M3, M5, M6, M7)
**Gate**: 9 passed, 0 failed

**Next steps**: none for this feature. Optional: a PowerShell-gated behavioral test (skipped when no `powershell`/`pwsh` is present) would turn the textual pins into execution checks on Windows dev machines.
