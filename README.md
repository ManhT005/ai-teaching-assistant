# GOS Academy — AI Teaching Assistant

Lean MVP for a teaching assistant using **FastAPI + PostgreSQL/pgvector + a simulated Zalo webhook**.

> **Status:** Demo ingestion, scoped hybrid retrieval, grounded generation, bounded event retries, transactional outbox, mock delivery and operator visibility are implemented. The webhook remains asynchronous: ACK means durable admission. No real Zalo integration or proof of exactly-once network delivery is claimed.

## Architecture

```text
Mock Webhook --> FastAPI (auth / binding / dedup / atomic quotas) --> PostgreSQL queue
                                                                        |
                                     Event worker --> Grounded Answer OR Ticket + Outbox
                                                                        |
                                         Delivery worker --> idempotent local mock inbox

Reviewed sources --> Ingestion --> pgvector + FTS --> Hybrid retrieval --> Evidence Gate
                                                                        --> LLM --> Validator
```

One FastAPI service, separate event and delivery worker processes, one PostgreSQL
service; no Redis, Celery or external message broker.

The internal Teacher & FAQ workflow supports class-scoped staff credentials,
ticket assignment, explicitly approved human replies through a separate mock
outbox, and independently reviewed FAQ publication through KnowledgeStore.
Run migration `0009_teacher_faq_workflow` before using it. See the
[workflow architecture](docs/architecture/teacher_faq_workflow.md) and
[local staff demo/runbook](docs/testing/teacher-faq-acceptance.md).

Follow-up interpretation uses at most five same-sender answered turns from the
same bound group/class within 20 minutes. The history only helps identify a
topic; answers still require fresh retrieval and exact reviewed-source quotes.
See `CONTEXT_*` limits in `.env.example`. Free semantic paraphrasing is disabled.

## Local setup (PowerShell)

Requirements: Python 3.11+; Docker Desktop with Docker Compose.

1. `Copy-Item .env.example .env`. Set `POSTGRES_PASSWORD`, `DATABASE_URL` with the **same password**, and separate long, random `MOCK_WEBHOOK_TOKEN` and `OPERATOR_API_TOKEN` values. Never commit `.env`.
2. `docker compose up -d db` and wait until DB is healthy.
3. `py -3 -m venv .venv`; ` .\.venv\Scripts\Activate.ps1`; `pip install -r requirements.txt`.
4. `alembic upgrade head`.
5. `python -m scripts.seed`.
6. `uvicorn app.main:app --reload`.

Check `http://127.0.0.1:8000/health`, `/ready` and API docs at `/docs`.

## Local demo admin console

Open `http://127.0.0.1:8000/demo/admin` after starting the local API, loading demo
materials and starting the event, mock delivery and teacher reply workers. Bind
the demo server to localhost. The console calls the same authenticated API
endpoints as Swagger and keeps the existing server-side permission checks.

1. In **Vận hành**, enter the local webhook and operator API tokens, send a
   question, then refresh the mock inbox after the workers process it. The inbox
   shows response text, class and citations. Replaying reuses the original event.
2. In **Ticket giảng viên**, use the class-scoped staff token and load tickets.
   Claim a ticket, save a reply draft and approve the saved revision. Refresh
   the ticket after the teacher reply worker sends it to the mock inbox.
3. In **Duyệt FAQ**, a teacher can propose from a resolved ticket and submit the
   draft. Keep the FAQ ID, switch to a distinct reviewer token and reopen that
   ID to review it. Switch to a staff token with the operator grant to publish
   the approved revision. The operator API token is only for metrics and inbox.
   See the [staff demo runbook](docs/testing/teacher-faq-acceptance.md) for
   credential setup and the embedding requirements for publication.

Credentials are entered into masked fields, cleared on reload and sent only as
headers to the same local server. Use demo data and keep token files off-screen.

## Mock webhook

The class is determined **only by the verified group binding in the database**, not by a class ID supplied in the payload.

```powershell
$body = @{
  event_id = "demo-001"; channel = "mock"; group_id = "mock-ai-automation"
  sender_id = "student-001"; text = "/hoi RAG khác fine-tuning thế nào?"
  received_at = "2026-11-09T19:30:00+07:00"
} | ConvertTo-Json

Invoke-RestMethod -Method Post -Uri http://127.0.0.1:8000/webhook/mock `
  -Headers @{"X-Webhook-Token" = "<your-local-token>"} `
  -ContentType "application/json" -Body $body
```

Expected: first event `accepted`, replay of the same `(channel,event_id)` `duplicate`. The webhook **only stores the event**; two independent workers process it and write a local mock delivery. Unknown/inactive groups return 403. Client-supplied `class_id` and unexpected fields return 422. In authorized groups, bot messages, messages without the `/hoi` command, and empty questions are ignored. `/hoiday` is not a trigger. Atomic fixed-window quotas default to 10 new events per sender and 100 per group per minute; duplicates and ignored events do not consume quota. A new over-limit event gets 429 with `Retry-After` and is rolled back.

Configure different high-entropy values for `MOCK_WEBHOOK_TOKEN` and `OPERATOR_API_TOKEN`. Set bounded event/delivery retry values in `.env.example`. Run each worker in a separate terminal after the API and database are ready:

```powershell
python -m scripts.run_event_worker --poll-interval 1
python -m scripts.run_delivery_worker --poll-interval 1

# Periodically prune quota windows older than two configured windows.
python -m scripts.cleanup_rate_limits
```

Use `--once --limit 10` for one batch. `scripts.process_pending_events --once` remains available for operator compatibility, but it is not needed for automatic processing. The event worker claims with a renewable-on-reclaim lease and fencing token, then releases its DB connection before generation. Transient errors use bounded exponential backoff; exhausted work becomes `FAILED_FINAL`. The outbox is committed atomically with the Answer or Ticket. Mock delivery records a deterministic idempotency key in PostgreSQL; `SENT` means the mock inbox accepted the record, not that a learner read it. A handoff acknowledgment says only that the request was received.

Operator-only endpoints require `X-Operator-Token` (separate from the webhook credential):

```text
GET /internal/demo/metrics
GET /internal/demo/events/{internal_event_id}
GET /internal/demo/deliveries?limit=20
```

Metrics are aggregate and preserve unknown cost as `null`. Event inspection omits sender and question. Delivery inspection exposes the mock reply/citations only to the authenticated operator. No endpoint lets a client choose a class.

`received_at` must include a timezone. It is stored as `source_received_at`; the server's intake time remains in `events.received_at`. Existing installations must run `alembic upgrade head` for migration `0002_source_received_at`.

## Sample events and materials

The supplied simulated events are preserved in `data/samples/sample_events.original.json`. Convert them without editing the original:

```powershell
python -m scripts.convert_sample_events data/samples/sample_events.original.json --output data/samples/mock_events.triggered.json --add-trigger
python -m scripts.convert_sample_events data/samples/sample_events.original.json --output data/samples/mock_events.raw.json
python -m scripts.check_sample_materials
python -m pytest -q
```

The converter writes JSON only; it never sends requests or emits credentials. Submit each item in the output `events` list as a separate authenticated `/webhook/mock` request. Class IDs in the original local fixture only choose a mock group; the runtime still authorizes using its database binding. The two checked-in output variants support question replay and ignored-message checks.

`data/samples/derived/` contains a DOCX/PDF excerpt and `knowledge_demo.xlsx` with 8 structured records. Seven records derive from supplied simulated materials; the new RAG–Fine-tuning draft remains `pending` and is excluded from eligible demo evidence. Per-class schedules are not `COMMON`. The PDF and DOCX share canonical source identity and are deduplicated before indexing.

The offline check parses 12 items, resolves them to 9 logical sections and reports `handoff / INCOMPLETE_EVIDENCE` for the comparison question. This checks the contract only; it does not run retrieval, create a ticket or answer the question. See [the agreed contracts and acceptance limits](docs/architecture/sample_contracts.md).

CI runs migrations, repeatable seeding, lint/format checks and PostgreSQL integration tests. Locally, PostgreSQL tests require `TEST_DATABASE_URL` pointing to a **dedicated test database**; otherwise those tests are reported as skipped.

## Demo knowledge ingestion

The original materials are **simulated test data**. `provided_sample` permits
demo indexing; it is not approval of production policy. Keep all five supplied
files in ignored `data/raw/`. The guide and event JSON are excluded from indexing.

```powershell
# Create a local whitelist with explicit section scopes and verified checksums.
python -m scripts.inspect_sources --write-manifest data/raw/manifest.local.json

# Download the pinned E5 revision once; subsequent commands use the local cache.
python -m app.knowledge.embeddings --download
alembic upgrade head

# Inspect uses the cached tokenizer; it does not load weights or access the DB.
python -m scripts.ingest_knowledge inspect --manifest data/raw/manifest.local.json
python -m scripts.ingest_knowledge ingest --manifest data/raw/manifest.local.json
python -m scripts.ingest_knowledge ingest --manifest data/raw/manifest.local.json
python -m scripts.ingest_knowledge status

# Operator-only diagnostics; class arguments are not a student authorization API.
python -m scripts.smoke_vector_search --class AUTO-N8N-01 --query "Em chạy n8n bị lỗi 401 Unauthorized"
python -m scripts.smoke_vector_search --class GS-KPI-01 --query "IMPORTRANGE có đồng bộ ghi chú và bình luận không?"

# Immediate revocation, including staged versions. Identical revoked input stays blocked.
python -m scripts.ingest_knowledge revoke --doc-id DOC-AUTO-01 --reason "Operator withdrawal"
```

The second ingest skips unchanged input. To rebuild unchanged eligible input, add
`--force`. After changing a source file, review the file, update its SHA-256 in the
local manifest and update its external version when appropriate. Internal numeric
versions increase even when the external label remains `TEST-1.0`. Never edit
source course content merely to satisfy the parser.

Only `provided_sample` and audited `approved_demo` content receives embeddings.
`pending` stays excluded. The tool has no automatic approval command. An explicit
`approved_demo` manifest decision requires `review_decision` with `reviewer`,
timezone-aware `reviewed_at` and `reason`; these decisions are stored with the
document version. XLSX indexing requires trusted metadata for every record;
worksheet cells cannot promote review or change scope.

Embedding uses `intfloat/multilingual-e5-small` at revision
`614241f622f53c4eeff9890bdc4f31cfecc418b3`, 384 dimensions, CPU, normalized vectors
and separate passage/query prefixes. Default chunks target 320 input tokens with
up to 40 tokens of overlap, including heading/prefix/special tokens. Model/cache
configuration is in `.env.example`; weights remain under ignored `.models/`.
There is no fallback model. Missing cached files cause a clear failure.

Migrations `0003` and `0004` preserve Day 1 tables. Legacy documents are disabled
until reviewed/reindexed; incompatible legacy vectors make migration fail
transactionally. Back up a populated database first. Downgrading removes review
metadata and disables documents; it does not restore the old searchable index.

For local acceptance after preparing a dedicated test database:

```powershell
# Set TEST_DATABASE_URL to a test database; never use a production database.
$env:ORIGINAL_SOURCES_MANIFEST = 'data/raw/manifest.local.json'
$env:RUN_LIVE_EMBEDDING = '1'
python -m pytest -q --basetemp=.pytest_cache/test-work
```

CI runs deterministic tests and real PostgreSQL 17 integration without downloading
model weights. Original-source and live-model checks are explicitly opt-in.
See the [ingestion contract](docs/architecture/ingestion_contract.md) and
[recorded acceptance results](docs/testing/rag-ingestion-acceptance.md).

## Hybrid retrieval

Apply `alembic upgrade head` to add migration `0005_hybrid_retrieval`. PostgreSQL
automatically creates lexical vectors for existing chunks; re-ingestion is not
required. The model, original data and ingestion lifecycle are unchanged.

Use an existing **operator-selected mock group binding**, resolved in PostgreSQL:

```powershell
python -m scripts.hybrid_search --group mock-ai-automation --query "Em chạy n8n bị lỗi 401 Unauthorized"
python -m scripts.hybrid_search --group mock-google-sheets --query "IMPORTRANGE có đồng bộ ghi chú và bình luận không?" --top-k 3

# Compare all three rankings on the frozen original-source corpus.
python -m scripts.evaluate_retrieval --check-target --output .pytest_cache/hybrid-evaluation.json
```

The CLI requires database access and is for operators only. Group names and the
mock webhook shared secret do not establish a student's group membership. There
is no public search endpoint or client-controlled class selector. Application
code calls `resolve_authorized_scope` only after authenticating the upstream
channel, then passes that scope to `HybridRetrievalService.search`.

The service normalizes/bounds questions, encodes with the ingestion E5 revision,
and applies one shared class/review/version/model SQL allowlist **before each
branch's candidate limit**. Both queries share a read-only snapshot. RRF merges
unique chunk rows, retaining citations and branch ranks. Ordinary logs and CLI
output exclude source passages; `--include-content` explicitly prints them for
operator inspection. Retrieval scores do not authorize an answer.

Default budgets are 10 candidates per branch, up to 6 technical lexical anchors,
`k_rrf=60`, final top-k 1–5 and questions up to 1000 characters within E5's token
limit. See `.env.example`. PostgreSQL `simple` is not a Vietnamese word segmenter
and does not remove accents. Questions without technical anchors use dense
results when the lexical branch is empty.

Local source-checked benchmark: **30 questions**, 26 answerable plus 4 explicitly
unscored no-evidence cases. Hybrid Recall@3 is **93.59%**, versus dense **95.51%**;
both have Hit@3 **100%** and zero cross-class hits. This is a small developer
benchmark with automated source review; independent human label review has not
been performed. See the [architecture](docs/architecture/hybrid_retrieval.md)
and [acceptance report with misses](docs/testing/hybrid-retrieval-acceptance.md).

## Grounded generation and teacher handoff

Apply migrations through `0007_event_reliability` with `python -m alembic upgrade head`.
Keep `LLM_PROVIDER=fake` for offline operation. To call Gemini, set
`LLM_PROVIDER=gemini`, `GEMINI_API_KEY` and `GEMINI_MODEL` in `.env` using
[.env.example](.env.example). The model `gemini-3.5-flash-lite` was smoke-tested;
an account-specific model can be configured.

```powershell
# Optional compatibility CLI for a direct single-batch operator run.
python -m scripts.process_pending_events --once --limit 5

# Explicit operator preview, including reply and citation provenance.
python -m scripts.process_pending_events --once --event-id 123 --preview
```

The internal event ID is the integer from the `events` table, not the external
event string. Terminal reruns return the saved decision. `--preview` prints to the
operator only; it sends no message to Zalo. Normal processing runs automatically
through `scripts.run_event_worker` and `scripts.run_delivery_worker`; due retries
and expired leases are recovered without per-event CLI calls.

Answers quote 1–3 complete eligible paragraphs. The model selects temporary quote
IDs; Python composes every claim from the original text and stable chunk identity.
Every substantive question term must still be covered by each eligible paragraph.
Selections with unknown/repeated IDs, extra model prose or excessive length are
rejected as a whole. Citation metadata is deduplicated in first-selected order.
Free paraphrasing and unsupported comparisons or causal explanations still
require handoff. For the narrow RAG–Fine-tuning comparison, an active
`approved_demo` source must explain both mechanisms in one quotable span. The
supplied draft remains pending. Class-deadline questions require an explicit
deadline from the bound class, and conflicting deadlines require handoff.
Lexical coverage is not semantic proof. Commercial policy
and missing evidence create an OPEN teacher ticket. Revoked authorization and
provider errors have separate failure states. An Answer and Ticket cannot coexist
for the same event. Token counts remain unknown (`null`) when metadata is missing;
no cost is invented.

For a dedicated, migrated PostgreSQL test database:

```powershell
$env:TEST_DATABASE_URL = '<test database URL; database name contains test or smoke>'
python -m pytest -q --basetemp=.pytest_cache/test-tmp

# Optional: at most two real Gemini calls using .env credentials and supplied
# simulated sources. Retrieval embeddings are deterministic in this smoke.
$env:RUN_LIVE_GENERATION = '1'
python -m pytest -q tests/test_generation_live.py --basetemp=.pytest_cache/live-tmp
Remove-Item Env:RUN_LIVE_GENERATION
```

See the [grounding ADR](docs/architecture/grounded_generation.md) and
[selection/composition ADR](docs/architecture/multi_claim_selection.md),
[multi-claim acceptance evidence](docs/testing/multiclaim-acceptance.md) and
[operator runbook](docs/testing/grounded-generation-acceptance.md).

## Project map

- `app/main.py` — API entrypoint
- `app/api/` — health, readiness and mock webhook
- `app/models.py` — class, channel mapping, documents, chunks, events, answers, tickets and usage
- `app/db.py` — one SQLAlchemy connection/session setup
- `app/knowledge/ingestion.py`, `app/knowledge/source_manifest.py` — parsers, trusted source metadata and canonical deduplication
- `app/knowledge/chunking.py`, `app/knowledge/embeddings.py` — token-aware section chunks and pinned E5 CPU vectors
- `app/knowledge/store.py` — staging, idempotency, atomic activation and revocation
- `app/retrieval/service.py`, `app/retrieval/lexical.py`, `app/retrieval/fusion.py` — shared scope, dense/FTS candidates and deterministic RRF
- `scripts/ingest_knowledge.py`, `scripts/smoke_vector_search.py` — operator CLI and scoped SQL diagnostic
- `scripts/hybrid_search.py`, `scripts/evaluate_retrieval.py` — operator hybrid CLI and frozen quality comparison
- `app/generation/` — contracts, evidence policy, safety, validation and orchestration
- `app/llm/` — Vietnamese prompt, deterministic fake and Gemini REST adapter
- `app/events/` — atomic processing, persisted tickets, usage and safe response text
- `app/delivery/` — transactional outbox, delivery fencing and idempotent mock inbox adapter
- `scripts/process_pending_events.py` — operator processing and explicit reply preview
- `scripts/run_event_worker.py`, `scripts/run_delivery_worker.py` — independent polling workers
- `app/api/internal_demo.py` — separately authenticated operator metrics and mock inbox
- `alembic/` — versioned database migration, including pgvector extension
- `scripts/seed.py` — seed two demo classes and mock group bindings
- `scripts/convert_sample_events.py`, `scripts/check_sample_materials.py` — local fixture adapter and parser checks
- `tests/` — offline and PostgreSQL integration tests
- `docs/architecture/` — design notes
- `data/samples/` — labelled simulated events and derived knowledge fixtures; raw course files remain excluded

## Roadmap

| Day | Deliverable |
| --- | --- |
| 1 | Skeleton, PostgreSQL/pgvector, migration, seed, webhook intake |
| 2 | Approved document ingestion and isolated retrieval by class |
| 3 | Grounded LLM answer, citations, insufficient-evidence handoff |
| 4 | Webhook processing/recovery, controls, metrics |
| 5 | Mandatory scenarios, report, demo and handover |

**Security note:** The mock webhook token is only for local tests. Real Zalo OA/GMF access must be authorized and is out of the MVP scope. Offline and live-provider verification are reported separately; deployment and hosted CI require their own evidence.

