# LESSONS - auto-maintained by scripts/lessons.py

> Machine-owned. Do NOT hand-edit. Changes are overwritten on the next `lessons.py` write.
> Canonical state lives in `.specs/lessons.json`. Edit lessons only via the script.
> promote_threshold=2 distinct features · window_days=45 · quarantine_threshold=2

## Confirmed (load these at Specify/Design)

Corroborated across multiple features. Safe to apply as guidance.

_none_

## Candidates (under observation - do NOT load as guidance yet)

Seen once or not yet corroborated. Tracked, not trusted.

### L-001 - Test each grouping trigger with input where it is not also the end of the sequence, so a trailing flush cannot mask it
- signal: `surviving_mutant` · recurrence: 1 feature(s) · scope: `tests` · harmful: 0
- features: dead-code-cleanup
- evidence: M10 farmcortes/pipeline.py:222 (tests)
- last seen: 2026-11-22T20:06:08Z

### L-002 - Test numeric thresholds at the exact boundary and just below it, not only with an overshoot
- signal: `surviving_mutant` · recurrence: 1 feature(s) · scope: `tests` · harmful: 0
- features: dead-code-cleanup
- evidence: M12/M13 farmcortes/pipeline.py:222 (tests)
- last seen: 2026-11-22T20:06:08Z

### L-003 - When a transformation must apply on every branch, assert it on each branch
- signal: `surviving_mutant` · recurrence: 1 feature(s) · scope: `tests` · harmful: 0
- features: dead-code-cleanup
- evidence: M5 farmcortes/pipeline.py:234 (tests)
- last seen: 2026-11-22T20:06:08Z

### L-004 - Prove deduplication behaviorally, not by counting source-text literals that mask behavioral mutants
- signal: `spec_precision_gap` · recurrence: 1 feature(s) · scope: `tests` · harmful: 0
- features: dead-code-cleanup
- evidence: tests/test_dead_code.py:88 (tests)
- last seen: 2026-11-22T20:06:09Z

### L-005 - When tests can only read source text, assert the whole decision line including its comparison and boolean operators, not a substring of it
- signal: `surviving_mutant` · recurrence: 1 feature(s) · scope: `tests` · harmful: 0
- features: worker-binary-pinning
- evidence: M3/M5/M6 farmcortes/worker.ps1:25,42,35 (tests/test_worker.py:67,82,83) (tests)
- last seen: 2026-11-22T20:21:28Z

### L-006 - When a guard must abort, assert the abort statement itself, not only the cleanup and message text
- signal: `surviving_mutant` · recurrence: 1 feature(s) · scope: `tests` · harmful: 0
- features: worker-binary-pinning
- evidence: M7 farmcortes/worker.ps1:44 (tests/test_worker.py:73) (tests)
- last seen: 2026-11-22T20:21:28Z

## Quarantined (failed when applied - ignore)

A confirmed lesson that recurred alongside failure. Kept for the maintainer to review.

_none_
