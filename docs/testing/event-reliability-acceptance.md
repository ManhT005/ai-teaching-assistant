# PR05 event reliability acceptance

**Base:** `origin/develop` at `4fac46ee4ea6aaaa9b120793b51e7d5eaad7b77e`
**Branch:** `feature/event-reliability-mock-delivery`
**Scope:** PostgreSQL event leases/recovery, transactional outbox, mock delivery,
atomic webhook quotas and operator-only inspection. No real Zalo transport.

## Local verification

- PostgreSQL 17 with pgvector, dedicated `gos_generation_test` database.
- Fresh per-test schemas upgraded through `0007_event_reliability`; populated
  upgrade/downgrade preserves existing Answer rows and gives legacy `PROCESSING`
  rows a 120-second recovery grace period.
- Full suite on the final implementation: **254 passed, 4 skipped**.
- Ruff lint and format checks pass for all changed Python modules, migration and
  tests. JUnit: `.pytest_cache/event-reliability-junit.xml` (local/ignored).
- E2E uses the existing deterministic FakeLLM and test database. It verifies
  authenticated webhook → worker → Answer/Ticket → outbox → mock receipt and
  operator inspection. No live provider or Zalo request was made.
- The four skips are the existing opt-in/live or private-source checks; they do
  not conceal a failure in this local suite.

## EVT-T01–T42 matrix

| ID | Result | Evidence |
| --- | --- | --- |
| T01 | PASS | `test_event_queue.py::test_one_event_has_one_live_claim_and_reclaim_fences_old_token` |
| T02 | PASS | Same test, two concurrent claimers |
| T03 | PASS | `test_ten_competing_claimers_only_acquire_available_events` (10 events / 10 workers) |
| T04 | PASS | `test_answer_ticket_usage_replay_and_cli` |
| T05 | PASS | Same test, terminal HANDOFF replay |
| T06 | PASS | `test_retryable_event_is_not_claimed_before_due_time` |
| T07 | PASS | `test_due_retry_claim_gets_new_fencing_token` |
| T08 | PASS | `test_event_retry.py` injected backoff/jitter/cap tests |
| T09 | PASS | Provider 429/500/503/timeout sanitization + retry worker tests |
| T10 | PASS | Provider 401/config failure is final and sanitized |
| T11 | PASS | `test_worker_reclaims_expired_processing_event` |
| T12 | PASS | `test_stale_generation_cannot_finalize_after_reclaim` |
| T13 | PASS | Stale owner failure cannot change the replacement lease |
| T14 | PASS | Event attempt exhaustion terminalizes as `RETRIES_EXHAUSTED` |
| T15 | PASS | Webhook duplicate persists one event |
| T16 | PASS | Concurrent duplicate intake charges quota once |
| T17 | PASS | Altered duplicate body does not replace stored event |
| T18 | PASS | Invalid token/group/forged class produce no admitted event |
| T19 | PASS | Bot, non-command, disabled class and inactive binding are ignored/rejected |
| T20 | PASS | Sender N/N+1 quota and `Retry-After` |
| T21 | PASS | Concurrent sender requests stay within group quota |
| T22 | PASS | Duplicate at full quota remains duplicate |
| T23 | PASS | Window boundary and expired-window cleanup |
| T24 | PASS | Answer plus outbox are persisted as one outcome |
| T25 | PASS | Open Ticket plus handoff acknowledgment outbox |
| T26 | PASS | Injected outbox error rolls back Answer and outbox |
| T27 | PASS | Concurrent delivery workers have one owner |
| T28 | PASS | Mock delivery receipt is idempotent |
| T29 | PASS | Temporary delivery failure retries without rerunning generation |
| T30 | PASS | Permanent and exhausted delivery failures become `DEAD`; outcome retained |
| T31 | PASS | Crash after mock receipt reuses the same receipt |
| T32 | PASS | Stale delivery lease cannot write receipt or mark the outbox |
| T33 | PASS | Group rebind suppresses dispatch |
| T34 | PASS | Disabled class suppresses dispatch |
| T35 | PASS | Revoked cited source suppresses dispatch |
| T36 | PASS | Cross-class E2E checks trusted binding, citations and destination |
| T37 | PASS (simulated) | n8n 401 test fixture is delivered through the mock inbox |
| T38 | SAFE FALLBACK | Tuition/policy question creates an open ticket and generic acknowledgment |
| T39 | SAFE FALLBACK | Injection request creates a ticket; mock output contains no requested secret |
| T40 | SAFE FALLBACK | RAG vs Fine-tuning remains a handoff because source material is still pending |
| T41 | PASS | Operator token, aggregate metrics, unknown cost and redaction tests |
| T42 | LOCAL PASS / HOSTED PENDING | Migration and full local suite pass; hosted CI awaits a pushed branch |

The seven demo outcomes are therefore: n8n 401 `PASS (simulated)`, tuition
`SAFE FALLBACK`, class isolation `PASS`, duplicate `PASS`, injection `SAFE
FALLBACK`, RAG-vs-Fine-tuning `SAFE FALLBACK / answer requirement blocked by
pending source`, and provider transport failures `PASS (stubbed)`. No generated
test result changes source review status or gold labels.

## Boundaries and remaining merge gates

- This branch is local and based on `origin/develop`; it has not been pushed, so
  hosted CI, PR review and reviewer sign-off remain pending.
- The test suite uses FakeLLM. Real Gemini multi-step retry cost/behavior is not
  claimed here; provider HTTP mapping has transport-mock coverage.
- `SENT` means a mock PostgreSQL receipt exists. External network delivery is
  at-least-once in principle; exactly-once Zalo delivery is not claimed.
- A crash after a provider call but before final commit can repeat that call and
  incur an un-attributed cost. Unknown token/cost values remain `null`.
- RAG-vs-Fine-tuning educational answer remains blocked on reviewed source
  content; the safe handoff is the expected result for this branch.

See [event reliability architecture](../architecture/event_reliability.md) and
the root [README](../../README.md) for startup, worker, operator and cleanup
commands.
