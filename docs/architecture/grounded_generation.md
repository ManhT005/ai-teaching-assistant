# ADR-GEN-01: grounded generation and teacher handoff

Status: implemented proposal; reviewer sign-off remains required.

The operator processes a stored event by internal ID on the existing feature branch.
The server resolves its current channel binding and checks the stored classroom.
There is no student-facing ask endpoint or caller-selected class authorization.

Generation uses a provider protocol, deterministic offline FakeLLM and optional Gemini
REST adapter. Set LLM_PROVIDER, GEMINI_API_KEY and GEMINI_MODEL in the environment.
The adapter follows Google's [generateContent contract](https://ai.google.dev/api/generate-content?hl=en).
No automatic network retry runs in this milestone; temporary failures become
FAILED_RETRYABLE. Permanent provider failures become FAILED_FINAL, without a ticket
that misrepresents infrastructure failure as missing knowledge. Usage is nullable
when the provider supplies no token metadata; cost is unknown.

The MVP supports **extractive grounded answers**, not unrestricted paraphrasing.
The pre-gate requires every substantive question term in one eligible paragraph,
with explicit handling of comparison, policy and unsafe requests. A provider may
select 1–3 allowed quote IDs. The catalog numbers at most eight full paragraphs
in retrieval/source order; Gemini's response schema contains ID enums only.
Python constructs AnswerDraft and each single-source claim from the catalog text.
The post-gate rejects unknown/repeated IDs, prose fields and answers over 6,000
characters without truncating paragraphs. The draft is checked again before
persistence; citation metadata follows first-selected order and is deduplicated.
This intentionally rejects paraphrases that cannot be verified by deterministic
rules. It preserves negation and avoids treating lexical overlap as entailment.
Term coverage is a conservative relevance heuristic, not semantic proof; a matching
paragraph can still be incomplete or misleading. Human review remains necessary.

See [ADR-GEN-02](multi_claim_selection.md) for the strict selection contract,
canonical provider input and immutable request allowlist. This adds no database
migration and does not change retrieval relevance or class authorization.

Sources are checked against current eligible database rows before generation and
again in the final transaction. Source rows and the binding/classroom are locked
at finalization, so concurrent revocation serializes with publication. No database
connection is held during generation. RRF and cosine are never confidence values.

PENDING -> PROCESSING uses a conditional atomic update. One processor wins; it
persists either Answer or Ticket in one transaction with usage and terminal state.
Database triggers serialize Answer/Ticket writes through the event row and reject
both outcomes for the same event. Terminal reruns return the persisted result.
Audit logs contain internal event ID, decision and reason, never raw prompts.

States: PENDING, PROCESSING, ANSWERED, HANDOFF, FAILED_RETRYABLE, FAILED_FINAL.
Reason codes and strict output schemas live in app/generation/contracts.py.
Policy questions use SENSITIVE_OR_POLICY; incomplete comparisons use
INCOMPLETE_EVIDENCE. Invalid citations and unsupported claims fail closed.

Pending RAG/Fine-tuning content remains pending. No source promotion, benchmark
qrels change, embedding model change, public class-selection API, Zalo delivery,
worker scheduler, retry loop or automatic reclaim is included. A crash can leave
PROCESSING stuck until the next reliability milestone. Exactly-once external
messaging is not claimed. Hosted CI and live-model results must be reported
separately from local deterministic tests.
