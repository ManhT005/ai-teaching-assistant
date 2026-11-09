# Teacher and FAQ workflow

PR07 extends `origin/develop` at `d6baaeb2d94f90b43b016e76b5a1136c4a77f9f2`.
The original event remains `HANDOFF` with its original Ticket and acknowledgment outbox.
A human response has its own reply, delivery outbox and local receipt. It never becomes an AI Answer.

## Staff authorization

The DB-admin CLI grants named local staff an actor record, class-specific roles and a randomly generated 256-bit credential. Only its SHA-256 digest is stored in PostgreSQL. Credentials expire and can be revoked; actors can be disabled and grants removed. `X-Teacher-Token` resolves identity on the server. The existing operator token is not a teacher credential.

Queue and object queries filter class grants in SQL. All mutations recheck active identity, classroom binding, role, ticket state and expected version. Unknown or unauthorized objects return 404. Credentials and source/reply text are never included in application log messages. Questions are bounded and redact email, phone and recognized secret patterns in staff previews; this is a limited demo minimizer.

## Ticket and delivery

`OPEN -> ASSIGNED -> IN_REVIEW -> REPLY_QUEUED -> RESOLVED`.
The assigned teacher may release an unapproved ticket to `OPEN`, or close a ticket with a reason as `CLOSED_NO_REPLY`. Reassignment requires the new teacher to author a fresh draft before approval. There is no automatic assignment timeout or assumed SLA.

Each user command includes `expected_version` and `idempotency_key`. Ticket row locking serializes claims and transitions. An existing key is checked against the authenticated actor and complete canonical request hash; reuse with another payload conflicts. The append-only action table records actor, version, timestamp and transition. Approved body/revision and submitted FAQ content are also protected by PostgreSQL immutability triggers.

Approval compares the returned exact reply revision and body hash, then atomically creates a separate teacher outbox. Recipients come from the stored Event and current binding. Delivery rechecks that binding, approved hash/revision, teacher credential and role. Text is human-labeled. The worker uses expiring leases and fencing tokens; an existing durable receipt is reused after a crash. Only receipt-backed finalization resolves a ticket. `DEAD` and `SUPPRESSED` leave it unresolved and appear in class-scoped metrics. `SENT` denotes local mock acceptance, not a learner read receipt.

## FAQ review

A teacher explicitly proposes new FAQ text from a resolved ticket with a sent human reply. Scope is pinned to that ticket's class. Reply approval grants no FAQ approval.

`DRAFT -> SUBMITTED -> APPROVED -> INDEXING -> PUBLISHED`, with `REJECTED`, `FAILED` and `REVOKED` alternatives. Editing appends a new immutable revision and resets review. A published source remains active while a replacement draft is reviewed. Review requires another authenticated actor with the class's `FAQ_REVIEWER` role, the exact current revision/hash, and a reason. Approval records the credential, actor and database timestamp. Tests use isolated synthetic staff identities and do not constitute real human sign-off.

Newly authored material remains `synthetic_demo`; authorized approval permits `approved_demo`, never `provided_sample`. Pricing, discount, refund, personal contact and known secret/injection patterns are blocked by this demo path. `COMMON` cannot be selected through these APIs. Separate organizational source review is needed for shared material and commercial policy.

## Artifact and atomic activation

An operator reserves a fenced publication lease for one approved revision. The exporter builds a deterministic XLSX with the exact `knowledge` columns and a trusted per-row manifest. Formula prefixes are rejected, cells are text, archive timestamps are canonical, and source checksums are verified through `load_manifest`.

The publisher calls the existing `KnowledgeStore.ingest`; it does not insert vectors or chunks itself. A trusted internal activation callback rechecks actor/reviewer, binding, candidate lease, revision, hash, document checksum, READY/active state and class-pinned chunks **inside the same transaction as source activation**. Publication status and the active document therefore commit together. No DB connection remains checked out while embedding runs. `KnowledgeStore` retains its original behavior for callers without a guard.

Each FAQ has a stable logical document ID. Editorial revision is the external version; internal document versions can have gaps after failed indexing attempts. Retry uses the same canonical artifact, and an already published revision returns its existing publication. Expired publication leases can be reclaimed after ten minutes. A superseded worker cannot activate an unapproved version.

Revocation holds the same document advisory lock as activation and changes FAQ status, publication state and KnowledgeStore source eligibility in one transaction. Retrieval and existing delivery-time citation checks then exclude that source. An ingestion failure leaves inactive staged/failed content and a sanitized error. A revised source needs fresh independent review; revoked candidates cannot be republished.

Urgent withdrawal checks the operator's grant to the original class and remains available when the origin binding is disabled. It sends no message and cannot change the recipient or scope. Publication and teacher delivery continue to require a valid current origin binding.

## Operations and retention

`GET /internal/teacher/metrics` filters counts by granted class and distinguishes ticket, reply-outbox and FAQ states, including unresolved delivery alerts. Human SLA remains unknown (`null`). Existing AI usage accounting is preserved.

Staff credentials are stored locally under ignored `.local/staff-credentials`; FAQ artifacts and manifests default to ignored `data/generated/teacher-faq`. Server configuration controls the artifact path. No retention duration is assumed: retain audit, reviewed artifacts and original Events until the maintainer sets the operating policy. Append-only audit rows cannot be modified/deleted through application DML. Back up PostgreSQL and artifacts before rollback. Downgrade withdraws indexed FAQ sources, retains original Events/Tickets and source history, and removes the staff workflow tables; workflow history is lost on downgrade.

Real Zalo delivery, SSO, a public dashboard, Google Sheets sync, automatic FAQ authorship, organizational policy approval and human teaching-quality ratings require separate work. Manual review is still required before merge and before treating any demo source as real curriculum.
