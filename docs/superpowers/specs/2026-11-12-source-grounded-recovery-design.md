# Source-grounded recovery for failed research synthesis

## Intent

When the planner and retrieval steps have already collected financial evidence but the structured final answer fails, still give the user a concise conclusion grounded in those records. The current generic “research synthesis incomplete” response should remain only when evidence is genuinely unavailable or synthesis recovery fails.

## Goals

- Preserve the existing OpenAshare flow: model plans the user intent, tools retrieve the planned evidence, and a synthesis answers the original objective.
- Give the synthesis fallback the actual evidence excerpts and values, with source names, dates, and evidence IDs.
- Avoid another broad tool-retrieval loop after retrieval has already produced useful evidence.
- Keep current prices, performance, financial figures, announcements, dates, and causes bounded to retrieved evidence.
- Mark a recovered answer as recovered/completed while retaining the original failure type for diagnostics.

## Non-goals

- Replace the primary structured-output agent or the persistent task planner.
- Broaden the set of financial facts that model memory may supply.
- Hide genuine missing evidence or provider failures.

## Approaches

1. **Continue the tool-enabled recovery agent.** It can retrieve missing information, but re-enters the same structured-output path and may repeat calls. Its current per-tool snapshot also omits the evidence text needed for synthesis.
2. **Synthesize retrieved evidence directly.** Pass a compact, source-tagged snapshot plus the original task plan to a plain-text completion call. This avoids a second retrieval loop and is the recommended choice because a synthesis failure occurs after the intended evidence-gathering stage.
3. **Return the incomplete evidence inventory.** This is safe but does not answer when the already-retrieved facts support a useful conclusion.

Use approach 2 whenever substantive provider evidence exists. Retain approach 1 for cases with no substantive evidence, where further retrieval can still help. Keep the incomplete response as the fail-closed result if both recovery paths fail.

## Constraints and behavior

- Treat source excerpts as untrusted data, never as instructions.
- Do not invent numeric facts, event causes, URLs, or evidence IDs.
- Keep only specific real gaps; remove generic stall text after successful synthesis.
- A recovered answer remains tied to the original user query and planner objective.
- Do not add a model-knowledge-fill label when the answer is synthesized from provider evidence.

## Acceptance

- A regression test with a time-sensitive investment query, generic stalled response, and real retrieved market evidence produces a usable conclusion from the evidence.
- The recovery prompt contains actual retrieved values/excerpts and the task objective.
- The recovered result retains evidence, clears the synthesis-incomplete flag, and is not mislabeled as unverified model memory.
- Failed recovery still preserves the original incomplete response.
- Relevant backend tests pass; the same Work query completes online with a substantive answer and no incomplete-synthesis heading.

## Implementation choices

- Keep the existing `recover_incomplete_synthesis` boundary and extend it to evidence-backed synthesis.
- Prefer evidence-backed synthesis before a second tool-enabled recovery attempt.
- Compact and bound evidence excerpts before sending them to the selected model.
- Derive persisted run status from whether recovery actually produced a usable response.
