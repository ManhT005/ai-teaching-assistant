# GEN-11–GEN-14: multi-quote selection acceptance

Date: 2026-11-08, Asia/Saigon. Branch: `feature/grounded-generation-handoff`.
Existing [PR #4](https://github.com/ManhT005/ai-teaching-assistant/pull/4) is extended;
no new PR or migration is needed. Baseline `d913dce` was fetched and fast-forwarded
before implementation. Baseline PR-triggered CI was verified successful:
[run 42353713823](https://github.com/ManhT005/ai-teaching-assistant/actions/runs/42353713823).

Validated implementation: `317fa881469633e2a96ecc40e8340a29f6d4a735`
(application/tests at `4f5d065`, plus clean-checkout evidence-directory handling).
The documentation commit follows the tested implementation. Python 3.11.9,
PostgreSQL 17/pgvector, dedicated `gos_generation_test` on `127.0.0.1:55432`.
Tests use disposable schemas, not the application's database.

## Local results

| Check | Actual result |
| --- | --- |
| Baseline full suite | 152 passed, 4 skipped |
| Upgrade full suite | **205 passed, 4 skipped**, 50.60 seconds |
| Archived clean-checkout full suite | **205 passed, 4 skipped**, 51.01 seconds |
| Contract and composer/provider units | 29 passed |
| Hardening/event targeted suite | 44 passed |
| New multi-claim PostgreSQL cases | 18 tests; included in passing full suite |
| Live Gemini ID-selection smoke | 1 test passed, four outcomes, exactly two requests |
| Generation/provider/event lint | PASS |
| Generation/provider/event formatting | PASS, 16 files checked |
| Retrieval, knowledge, migration and frozen evaluation-data diff | Empty |
| Implementation hosted CI on `317fa88` | **PASS: 205 passed, 4 skipped**, 16.78 seconds; [run 42467385577](https://github.com/ManhT005/ai-teaching-assistant/actions/runs/42467385577) |
| Final documentation-only HEAD | Use [PR HEAD checks](https://github.com/ManhT005/ai-teaching-assistant/pull/4/checks) for its separate authoritative status |
| Independent format/source/semantic review | Still required; no human approval is claimed |

The four default skips remain: live Gemini opt-in, live E5 opt-in and two ignored
original-source tests. Gemini was explicitly run separately. The unchanged E5
contract was verified in the preceding Day 3 work; this extension does not claim a
new combined E5-plus-Gemini quality evaluation. Original private files are still
unavailable, so their two tests remain blocked rather than replaced with fixtures.

The first extension CI run exposed a missing evidence-output directory in a
clean checkout. Commit `317fa88` creates it explicitly; the subsequent hosted run
and archived-checkout full suite pass. The archived run initially hit the known
Windows sandbox temp-directory restriction in four parser fixtures; rerunning
outside that restriction completed the full suite. No source or safety check was
relaxed to resolve either environment issue.

Commands used, with DATABASE_URL and TEST_DATABASE_URL set to the dedicated test DB:

```powershell
python -m alembic upgrade head
python -m pytest -q --basetemp=.pytest_cache/multiclaim-full-tmp --junitxml=.pytest_cache/pr04-multiclaim.xml
python -m ruff check app/generation app/llm app/events tests/test_generation_multiclaim.py
python -m ruff format --check app/generation app/llm app/events

$env:RUN_LIVE_GENERATION = '1'
python -m pytest -q tests/test_generation_live.py --basetemp=.pytest_cache/multiclaim-live-tmp
Remove-Item Env:RUN_LIVE_GENERATION
```

## What changed

The only model output is `{needs_handoff, quote_ids}`. A request-local catalog
maps q001…q008 to complete allowed paragraphs and stable chunk identity. Provider
and validator use identical source/paragraph ordering. Unknown or repeated IDs,
contradictory handoff selections and extra prose reject the whole output.

The backend copies 1–3 selected paragraphs into single-source claims in selection
order. Joined text is limited to 6,000 characters, including separators. At the
boundary, 2,000 + 2,000 + 1,998 characters plus two newlines passes; three 2,000
character paragraphs fail. Nothing is truncated. Claims preserve negation.

Allowlist data is copied and frozen. The composed draft is validated again before
the existing locked database scope/source check. Citation metadata is deduplicated
by chunk in first-selected order. Temporary quote IDs never replace chunk IDs in
Answer.source_chunks. PipelineDecision, event states, webhook ACK, usage audit and
Answer/Ticket XOR retain their existing contracts.

## Matrix coverage

| Plan cases | Evidence |
| --- | --- |
| MC-01–02 | Stable 1/3 quote catalogs, original source order and unchanged provenance |
| MC-03–04, MC-12 | Pure 1/2/3 composition and persisted 2 same-chunk, 2 different-chunk, 3-claim answers |
| MC-05–11 | Four IDs, empty/contradictory selection, unknown IDs, duplicate IDs, string list and model prose rejected; invalid subsets never become partial answers |
| MC-13–15 | Equal text from two chunks keeps two sources; negation retained; length/paragraph bounds fail closed |
| MC-16–19 | Foreign class cannot answer; pending/revoked/changed content/version and changed binding during generation block publication |
| MC-20–22 | Duplicate intake, terminal replay, two concurrent multi-claim processors, provider timeout/HTTP failures and injection regressions all pass |
| MC-23–27 | Existing 401, cross-class, tuition, comparison and out-of-curriculum expected decisions/reasons remain unchanged |
| MC-28–29 | Stable replay IDs/citations with one provider call; ordinary CLI output excludes text, preview is explicit, logs remain sanitized |
| MC-30 | Latest PR checks must be successful on the final published HEAD before merge |

Tests are in `test_generation_contracts.py`, `test_generation_composer.py`,
`test_generation_selection_safety.py`, `test_generation_multiclaim.py` and the
existing generation/provider/event/evaluation regressions. These assertions are
developer regression evidence, not independent correctness accuracy.

New integration sources are explicitly synthetic and temporary. Their review
records say automated fixture eligibility, not GOS/human approval. Existing course
source approval status, retrieval qrels and generation expected outcomes were not
changed to improve results.

## Sanitized examples and live verification

[Synthetic three-claim preview](multiclaim-synthetic-preview.json) demonstrates
three selected paragraphs with citation sequence `[1], [2], [2]`: two claims share
one chunk, so only two metadata citations are persisted. This is synthetic test
text, not an excerpt of private course material.

[Live evidence](multiclaim-live-smoke.json) records `gemini-3.5-flash-lite` on
2026-11-08 with deterministic retrieval embeddings and supplied simulated sources:

| Query | Result | Prompt tokens | Completion tokens |
| --- | --- | ---: | ---: |
| 401 Unauthorized | ANSWERED, AUTO-03, one selected ID | 400 | 32 |
| SUMIFS | ANSWERED, GS-02, one selected ID | 429 | 32 |
| RAG versus Fine-tuning | HANDOFF / INCOMPLETE_EVIDENCE; no model request | unknown | unknown |
| Tuition discount | HANDOFF / SENSITIVE_OR_POLICY; no model request | unknown | unknown |

The live model selected one quote for each answer. This verifies real ID-only
schema compatibility; real-model 2/3-quote selection was not observed in this
smoke. Multi-quote behavior is demonstrated by deterministic provider/composition
tests and actual PostgreSQL persistence, not labelled as a live semantic result.
No currency cost is claimed; only returned usage metadata is reported.

## Limits and merge gates

- This remains **multi-quote extraction**, not free paraphrase, semantic entailment
  or multi-intent question decomposition. Each paragraph still must cover all
  substantive question terms. Combining partial word coverage across sources is
  explicitly tested to remain a handoff.
- Multiple supported paragraphs can still be repetitive, incomplete or poorly
  ordered pedagogically. The sample is reviewable; independent teaching-quality
  approval remains outstanding.
- The RAG/Fine-tuning source remains pending. Safe handoff passes its regression,
  while the required substantive comparison answer is still unmet.
- No retrieval/index/embedding change, new migration, public API, memory, worker,
  scheduler/retry, delivery adapter or Zalo send is included. Existing crash/retry
  limitations remain documented in the Day 3 report.
- Final hosted CI and reviewer sign-off are required before merge. Commits are
  pushed to the existing branch; this work does not authorize automatic merge.
