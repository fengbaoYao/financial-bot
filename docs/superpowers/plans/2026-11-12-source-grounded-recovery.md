# Source-grounded synthesis recovery plan

## Files and responsibilities

- `api/financial_bot/gap_synthesis.py`: build a bounded snapshot from real evidence, detect substantive source data, and synthesize evidence-backed answers without model-fill labeling.
- `api/main.py`: try evidence synthesis before starting another tool-enabled agent recovery; pass the original task plan.
- `api/research_runs.py`: classify a successful recovery as completed while preserving its original failure type for diagnosis.
- `tests/test_gap_synthesis_recovery.py`: lock the investment-query failure mode, evidence visibility, no-memory boundary, and fail-closed behavior.
- `tests/test_research_runs.py` (or existing equivalent): verify persisted status reflects recovery outcome.

## Tasks

1. Add a failing regression test using an investment question, generic incomplete response, and source evidence with concrete price facts. Assert recovery replaces the canned response, receives source text and task objective, retains citations/evidence, and does not add a model-fill label.
2. Extend `gap_synthesis` with a bounded source-backed evidence predicate and include relevant evidence excerpts in its JSON snapshot. Keep user/portfolio-private fields excluded; mark excerpts as untrusted.
3. Use a source-only completion prompt when source evidence exists. Filter generic stall gaps, preserve specific gaps, and set `synthesis_incomplete=false`, `answer_basis=source_data`, and the evidence-synthesis recovery marker. Preserve the existing labeled stable-background fill behavior only when no source evidence exists and the query allows it.
4. In `main.py`, attempt this bounded synthesis before another tool-enabled recovery when substantive source data exists; pass the existing plan context. Leave the old recovery order for evidence-free cases.
5. Persist `completed` only when synthesis recovery succeeded; retain the originating failure code in diagnostics. Add a focused status test.
6. Run the focused regressions, the existing recovery/planner suites, syntax and diff checks, then deploy the changed backend files with a backup and verify health, file hashes, and the same Work query.

## Review focus

- Empty, unavailable, and identity-only tool results must not be treated as substantive evidence.
- Evidence excerpts must be bounded and serialized as valid JSON; truncation must not corrupt the snapshot.
- Existing stable-background gap recovery must retain its unverified model-fill label.
- A failed text synthesis must leave the original incomplete response intact.
