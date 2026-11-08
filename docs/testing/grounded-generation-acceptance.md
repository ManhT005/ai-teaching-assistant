# Grounded generation and teacher handoff acceptance

Update 2026-11-08: GEN-11–GEN-14 replaces the one-paragraph provider contract with
ID-only selection and deterministic composition of 1–3 full quotes. Current
verification is **205 passed, 4 skipped**; see the
[multi-claim report](multiclaim-acceptance.md) and
[selection ADR](../architecture/multi_claim_selection.md).
The sections below preserve the original Day 3 evidence and operator runbook.

Date: 2026-11-07, Asia/Saigon. Branch: `feature/grounded-generation-handoff`.
Validated implementation: `ad1b071` (with `30c6e0d` for constrained generation/live
regression and a final key-pattern normalization fix). This report and accompanying
documentation follow that implementation. These are developer verification results, not independent
reviewer approval or official GOSTECH acceptance thresholds.

## Result

| Check | Result | Evidence |
| --- | --- | --- |
| Full PostgreSQL 17 regression | PASS: 152 passed, 4 skipped | `python -m pytest -q --basetemp=.pytest_cache/key-pattern-final-tmp --junitxml=.pytest_cache/grounded-generation.xml` |
| Live Gemini smoke | PASS: four decisions, two actual requests | [Sanitized JSON evidence](grounded-generation-live-smoke.json) |
| Pinned cached E5 smoke | PASS: 1 test | `RUN_LIVE_EMBEDDING=1`, `tests/test_live_embeddings.py` |
| Generation/provider/event lint | PASS | Ruff over new modules, CLI, migration and generation tests |
| Application formatting | PASS | `python -m ruff format --check app` |
| Migration 0006 | PASS | Fresh schema, populated 0005, downgrade/upgrade, repeated upgrade, XOR concurrent writers |
| Hosted GitHub Actions on pushed HEAD `304204d` | PASS: 152 passed, 4 skipped, 0 failed | [Push CI run 42352761912](https://github.com/ManhT005/ai-teaching-assistant/actions/runs/42352761912), PostgreSQL 17 migrations and deterministic tests |
| Independent reviewer approval | PENDING_REVIEW | ADR and developer labels require review |

The four skips in the default full suite are the opt-in live Gemini test, opt-in
live E5 test and two original-source tests requiring ignored `data/raw` files.
Both opt-in model tests were run separately and passed. The two original-source
tests remain BLOCKED_MISSING_ORIGINAL_FILES. The supplied derived DOCX/PDF/XLSX
fixtures and their regressions pass; original course-file verification is not
substituted with those fixtures.

Local tests used a dedicated `gos_generation_test` database, PostgreSQL 17 with
pgvector on `127.0.0.1:55432`. Tests allocate and drop disposable schemas. No
application database or source approval status was modified. On this Windows
machine, using `127.0.0.1` avoided slow localhost connection fallback. Pytest used
a workspace-local basetemp because the default Windows temp directory was denied.

## Mandatory demonstration scenarios

| Scenario | Actual outcome | Result |
| --- | --- | --- |
| n8n HTTP 401 | ANSWERED, complete authentication quote with warning against posting tokens; AUTO-03 citation | PASS, deterministic and live Gemini |
| RAG versus Fine-tuning | HANDOFF / INCOMPLETE_EVIDENCE; OPEN ticket; pending comparison unchanged | SAFE FALLBACK ONLY — full mandatory teaching-answer scenario not yet met because an approved comparison source is unavailable |
| Tuition discount | HANDOFF / SENSITIVE_OR_POLICY; OPEN ticket; no price invented | PASS |
| Feature outside the curriculum | HANDOFF / INCOMPLETE_EVIDENCE | PASS |
| Same question from AUTO and GS | AUTO can answer; GS cannot cite AUTO; COMMON restricted by eligibility | PASS |
| Duplicate mock event | One stored event, one final Answer; replay does not generate again | PASS, mock webhook to CLI |
| Prompt injection | Unsafe questions/chunks rejected; citation/claim validator remains active | PASS, bounded patterns |

The generation dataset is separate from retrieval qrels:
`data/samples/generation_eval_cases.json` contains seven scoped regression cases,
labelled `developer_reviewed`. All seven expected persisted outcomes pass. This is
not an independent answer-correctness accuracy estimate. Retrieval qrels, review
labels, embedding model and 384 dimensions were preserved.

## Security and failure matrix

| Plan IDs | Automated evidence |
| --- | --- |
| GEN-T01–T05 | `test_generation_grounding.py`, `test_generation_evaluation.py`, existing COMMON/class authorization tests |
| GEN-T06, T16 | `test_duplicate_webhook_to_cli_persists_one_decision`: authenticated intake, duplicate event, client class rejected, CLI decision |
| GEN-T07–T08 | Unsafe question and source injection regressions; ordinary credential-sharing warning remains safe |
| GEN-T09–T10 | Fabricated ID, unsupported claim, dropped negation and added unvalidated prose rejected |
| GEN-T11 | Pending chunks, altered version/content/scope and revoked source rejected before generation |
| GEN-T12 | Source revoked during generation and binding changed during generation prevent answer persistence; known usage retained |
| GEN-T13 | Two concurrent processors: one claim, one provider call; independent Answer/Ticket writers: one saved, one rejected |
| GEN-T14 | Mock HTTP timeout/429/500/503 normalize to retryable; 401 to final; no invented answer or usage |
| GEN-T15 | Fake secret in provider error and secret question excluded from logs; Settings masks key; Gemini sends key only in header |
| GEN-T17–T18 | Revoked binding blocks pipeline; terminal Answer/Handoff reruns preserve IDs, counts and attempts |
| GEN-T19 | Malformed JSON, missing fields, extra fields and invalid types fail closed |
| GEN-T20 | Local PostgreSQL regression recorded; pushed-head hosted CI PASS with 152 passed / 4 skipped / 0 failed, deterministic embeddings and no live provider credentials |

Negative tests accept no fabricated citations, foreign-class/pending/revoked
sources or unsupported policy answers. These are assertions over the tested cases,
not an assertion of zero risk for arbitrary inputs.

## Live provider evidence

The successful run used the user's environment credentials and configured
`gemini-3.5-flash-lite`. Two real requests returned usage metadata:

| Case | Outcome | Section | Prompt tokens | Completion tokens |
| --- | --- | --- | ---: | ---: |
| HTTP 401 | ANSWERED | AUTO-03 | 406 | 411 |
| SUMIFS | ANSWERED | GS-02 | 484 | 263 |
| RAG comparison | HANDOFF / INCOMPLETE_EVIDENCE | none | no provider call | no provider call |
| Tuition policy | HANDOFF / SENSITIVE_OR_POLICY | none | no provider call | no provider call |

This smoke uses **real Gemini with deterministic test embeddings** and supplied
simulated source files. The separate cached E5 test verifies the pinned model;
this is not a claim of full live E5-plus-Gemini or external Zalo E2E quality.
No currency cost is reported because a current, reviewed price schedule was not
configured. Missing token metadata is stored as NULL, never an invented zero.

## Operator runbook

1. Fill `.env` from `.env.example`; use `fake` offline or configure the Gemini
   provider, API key and model. Never commit `.env`.
2. Run `python -m alembic upgrade head`, `python -m scripts.seed`, then the existing
   approved-source ingestion workflow. Keep pending synthetic comparisons pending.
3. Submit an authenticated mock `/hoi` event using the README example.
4. Run `python -m scripts.process_pending_events --once --limit 5`. Default output
   contains audit metadata without reply/source text.
5. To inspect a result, use `--once --event-id <internal integer ID> --preview`.
   Preview is operator output, not outbound delivery.
6. Review OPEN tickets with a DB-privileged operator connection, for example:

```sql
SELECT t.id, c.code, e.body AS question, e.received_at,
       t.status, t.reason_code, t.assigned_to, t.created_at
FROM tickets t
JOIN events e ON e.id = t.event_id
JOIN classes c ON c.id = t.class_id
WHERE t.status = 'OPEN'
ORDER BY t.created_at, t.id;
```

The question and sender remain in the original event, avoiding copies in audit
logs. Ticket reason, assignment, resolution and timestamp persist in the database;
there is no teacher dashboard/notification or FAQ approval UI in this milestone.

For tests, set TEST_DATABASE_URL to a dedicated database whose name contains
`test` or `smoke`, migrate that database, then run the commands above. Live Gemini
is explicitly enabled with `RUN_LIVE_GENERATION=1` and makes at most two requests
per test run. Remove that environment variable before the default offline suite.

## Limits and remaining review gates

- This is a conservative **extractive paragraph** MVP. Every substantive question
  word must appear in one allowed paragraph. General paraphrasing, comparison and
  causal explanation require handoff. A complete quote can still be irrelevant,
  misleading, wrong or incomplete; exact matching is not semantic entailment.
- Injection/secret detection uses bounded rules and can have false positives or
  miss unfamiliar attacks. The model receives no environment key or raw event
  identifiers. Whole-quote/citation checks are independent of those patterns.
- Source, chunk, class and binding rows are locked during final revalidation.
  Revocation commits before finalization invalidate the answer; later revocation
  cannot retroactively change a previously persisted historical answer.
- No connection is held during provider calls. A crash after claim may leave
  PROCESSING stuck; lease/reclaim, retry/backoff, scheduler and dead-letter handling
  remain Day 4 work. FAILED_RETRYABLE is recorded but not automatically retried.
- Answer/Ticket XOR is enforced by transaction plus database triggers. Direct
  operator edits of event status, deletes or legacy data are outside normal
  processor guarantees. Migration aborts on conflicting pre-existing outcomes.
- Downgrading 0006 removes new audit columns and maps NULL usage to legacy zero to
  satisfy the old schema; events, answers, tickets and content are preserved.
- Hosted CI passed for pushed implementation HEAD `304204d` (152 passed, 4 skipped).
  The later documentation-only correction requires its own fresh CI before merge.
  Original-source-file verification and independent review remain open gates.
  No merge, production deployment or outbound Zalo send is claimed.
