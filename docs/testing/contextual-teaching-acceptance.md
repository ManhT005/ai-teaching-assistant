# Contextual teaching acceptance

## Local verification

Review follow-up run: **345 passed, 4 skipped** in 82.09 seconds on a fresh PostgreSQL/pgvector test database. This includes 11 PostgreSQL context-security tests, 12 planner/pipeline tests and 40 synthetic fixture cases. The four provider/environment gated skips remain; human teaching quality has not been measured.

Use the repository virtual environment and a dedicated PostgreSQL test database with pgvector:

```powershell
$env:TEST_DATABASE_URL = $env:DATABASE_URL
.\.venv\Scripts\python.exe -m pytest -q
```

The integration fixture creates an isolated schema and migrates it through Alembic `head`, including `0008_contextual_teaching`. Do not point `TEST_DATABASE_URL` at a production database.

## Implemented behavior

- Bounded same-sender, same-channel/group/class history uses only answered turns with a mock `SENT` answer outbox and currently eligible citations.
- Query resolution is deterministic, retry-stable and does not call a rewriting model.
- An unresolved elliptical question hands off; history is never used as answer evidence.
- Safe server headings wrap the existing verbatim quote output; citation metadata and source version remain unchanged.
- The planner checks claim-to-citation spans, requires a quoted example for example requests, orders example paragraphs first and records intent/coverage for audit.
- Semantic paraphrasing and automatic curriculum approval are not implemented.

Run focused context tests with `python -m pytest -q tests/test_context_rewriter.py tests/test_context_reliability.py tests/test_teaching_coverage.py tests/test_generation_events.py tests/test_event_delivery_e2e.py`. The committed evaluation cases are synthetic, pending review and excluded from any pass-rate claim. A separately authored and independently reviewed holdout, human teaching ratings, P50/P95 latency and cost study remain merge-review inputs, not measured results in this change. `tests/test_migrations_rag.py` covers the new migration's upgrade and downgrade.

Review follow-up checks:

```powershell
python -m pytest -q tests/test_context_security.py tests/test_teaching_planner.py tests/test_teaching_evaluation.py
```

The security suite exercises different class/sender/group/channel, 20-minute TTL expiry, unsent answers, revoked sources and bindings, frozen snapshots and provider retries. The fixture runner executes all 40 cases (32 development plus eight unsealed holdout candidates). Source approvals remain pending; these fixtures do not modify frozen retrieval qrels or approve curriculum. Human teaching ratings and an independently authored sealed holdout are still required before making teaching-quality or semantic-paraphrasing claims.

## Demo path

Run `tests/test_context_rewriter.py` to see a 401-to-403 follow-up preserve the new status code, new-topic clearing, ambiguous-reference handoff and no-history handoff. Existing event delivery tests cover Answer/Ticket and mock outbox behavior. Source revocation is rechecked at finalization and delivery by existing gates.
