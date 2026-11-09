# Teacher and FAQ acceptance

## Environment

Baseline: `origin/develop` at `d6baaeb2d94f90b43b016e76b5a1136c4a77f9f2`, **345 passed, 4 skipped** on a dedicated PostgreSQL 17/pgvector database. New tests create disposable schemas and run actual Alembic migrations. Fake embeddings/provider and isolated synthetic staff approvals are test fixtures only. No real teacher approval or real Zalo send was performed.

Final local regression: **379 passed, 4 skipped** in 157.26 seconds, including 34 new workflow/migration cases. A newly created PostgreSQL 17 database was migrated from empty through 0009; seeding ran twice, the offline sample check passed, and Ruff check/format passed across all 27 relevant Python files. The full run's JUnit output was used to build `teacher-faq-evaluation.json`. Hosted CI at the final branch head and independent human/security review remain pending.

```powershell
alembic upgrade head
python -m scripts.seed
$env:TEST_DATABASE_URL = '<dedicated postgresql URL whose database name contains test>'
python -m pytest -q tests/test_teacher_workflow.py tests/test_faq_workflow.py tests/test_teacher_faq_migrations.py
python -m pytest -q
```

## Tested contracts

| Area | Evidence |
|---|---|
| Migration | Populated 0008 OPEN ticket survives upgrade/downgrade/upgrade; original Event remains HANDOFF |
| Identity | Missing/wrong/expired/revoked credentials; supplied actor field rejected; other-class IDs hidden |
| Ticket state | Concurrent claim has one winner; stale revision/key reuse conflict; explicit close does not claim delivery |
| Reply delivery | Separate receipt/outbox, approval replay, lease recovery after receipt, binding/credential/hash suppression, DEAD remains unresolved |
| Audit | Actor/version/request hashes, DB append-only actions and submitted content immutability |
| FAQ review | Pending/rejected absent from index, author cannot self-review, wrong-class reviewer denied, stale hash rejected |
| Publication | Trusted artifact/manifest, checksum and formula rejection, exact class/locator/version, failed embedding and controlled retry |
| Race handling | Reviewer revocation or candidate withdrawal during embedding prevents activation |
| End to end | Human reply -> independent fixture review -> FAQ retrieval -> grounded Answer -> source revoke -> stale citation delivery suppressed |
| Governance | Synthetic origin retained; policy/secret/markup/formula input blocked; no implicit COMMON |

## Manual local demo

Provision separate credentials for the actual local participants; this command grants permissions but performs no approval:

```powershell
python -m scripts.seed_teacher_demo --alias teacher-a --class-code AUTO-N8N-01 --role TEACHER
python -m scripts.seed_teacher_demo --alias reviewer-b --class-code AUTO-N8N-01 --role FAQ_REVIEWER
python -m scripts.seed_teacher_demo --alias operator-c --class-code AUTO-N8N-01 --role OPERATOR
python -m scripts.run_teacher_reply_worker --poll-interval 1
```

Each credential is written to its own ignored file; the key is not printed. Each participant uses their own file. Use the local API docs or the localhost-only JSON client:

```powershell
python -m scripts.staff_request /internal/teacher/tickets --token-file .local/staff-credentials/<teacher-file>.token
python -m scripts.staff_request /internal/teacher/tickets/<ticket>/claim --method POST --token-file .local/staff-credentials/<teacher-file>.token --body-file .local/claim.json
```

1. Submit an unsupported authenticated mock question, then run the normal event and acknowledgment workers. Save its ticket ID.
2. Teacher lists/detail/claims it with current `version` and a unique key. Draft with `body`; read the returned `reply.revision` and `reply.body_hash`.
3. After the teacher reviews the text, call `approve-reply` with `expected_version`, unique key, `reply_revision` and `approved_body_hash`. Run the teacher worker; check `/replies/{id}/delivery` and confirm ticket `RESOLVED`. Original Event stays HANDOFF and has no AI Answer.
4. Teacher creates `/internal/faq/candidates` with `ticket_id`, current ticket version, question, answer, topics and a unique key. This does not publish. Submit with the current FAQ revision.
5. The separate reviewer reads the candidate, checks its exact content/scope, and manually calls `/review` with `decision`, `candidate_body_hash`, current FAQ revision as `expected_version`, reason and unique key. If no reviewer is available, leave SUBMITTED.
6. Operator calls `/publish`. Confirm PUBLISHED, matching document identity, and scoped retrieval. A new learner question only becomes an Answer if the existing EvidenceGate has enough source coverage.
7. Operator calls `/revoke` with revision, reason and unique key. Confirm the source disappears from current retrieval and old citations are suppressed. A staff member granted only GS cannot read or act on these AUTO objects.

Use the returned hashes, which are SHA-256 over canonical JSON content, rather than hashing differently serialized text. Ticket commands use ticket `version`; FAQ editing/review/publication use FAQ `revision` as `expected_version`. Release is available for unapproved work. Reopen and automated assignment expiry are not provided.

```json
{"expected_version": 1, "idempotency_key": "manual-claim-unique-key"}
```

Operational recovery: inspect class-scoped metrics and delivery error codes. Fix authorization before making a new human decision; SUPPRESSED/DEAD is not success. Do not edit approved DB rows or reset sent outbox rows. Recover failed FAQ publication through `/publish` after correcting the infrastructure; an active lease returns conflict until its ten-minute expiry. Urgent withdrawal uses `/revoke`, including while indexing. Credential revocation uses `python -m scripts.seed_teacher_demo --revoke-credential-id <id>` with DB-admin access.

## Review boundary

Local tests do not supply human content approval, production security sign-off, teaching-quality scores or hosted CI results. Live E5/Gemini/provider checks remain opt-in and are reported as skips when not configured. No frozen retrieval qrels or provided curriculum is changed by this workflow.
