# GOS Academy — AI Teaching Assistant

Lean MVP for a teaching assistant using **FastAPI + PostgreSQL/pgvector + a simulated Zalo webhook**.

> **Status:** Day 1 skeleton with the sample-data contracts agreed on 2026-11-05. Health API, schema, seed, authenticated event intake, offline PDF/DOCX/XLSX parsers and a demo evidence gate are implemented. Embedding/indexing, retrieval, LLM answers, persisted teacher handoff, retries and reporting are **not implemented yet**. No real Zalo integration.

## Architecture

```text
Mock Zalo Webhook --> FastAPI (auth / class mapping / dedup) --> PostgreSQL
                                                              |
Approved PDF/DOCX/FAQ --> Ingestion (Day 2) --> pgvector <--- RAG (Day 2-3)
                                                              |
                                                 Answer / Teacher Ticket (Day 3)
```

One FastAPI process, one PostgreSQL service, no Redis, Celery or microservices.

## Local setup (PowerShell)

Requirements: Python 3.11+; Docker Desktop with Docker Compose.

1. `Copy-Item .env.example .env`. Set `POSTGRES_PASSWORD`, `DATABASE_URL` with the **same password**, and a long, random `MOCK_WEBHOOK_TOKEN`. Never commit `.env`.
2. `docker compose up -d db` and wait until DB is healthy.
3. `py -3 -m venv .venv`; ` .\.venv\Scripts\Activate.ps1`; `pip install -r requirements.txt`.
4. `alembic upgrade head`.
5. `python -m scripts.seed`.
6. `uvicorn app.main:app --reload`.

Check `http://127.0.0.1:8000/health`, `/ready` and API docs at `/docs`.

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

Expected: first event `accepted`, replay of the same `(channel,event_id)` `duplicate`. This **only stores the event**; no AI answer is generated yet. Unknown/inactive groups return 403. Client-supplied `class_id` and unexpected fields return 422. In authorized groups, bot messages, messages without the `/hoi` command, and empty questions are ignored. `/hoiday` is not a trigger.

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

CI runs migrations, repeatable seeding and webhook tests against PostgreSQL/pgvector. Locally, PostgreSQL tests require `TEST_DATABASE_URL` pointing to a **dedicated migrated test database**; otherwise those tests are reported as skipped.

## Project map

- `app/main.py` — API entrypoint
- `app/api/` — health, readiness and mock webhook
- `app/models.py` — class, channel mapping, documents, chunks, events, answers, tickets and usage
- `app/db.py` — one SQLAlchemy connection/session setup
- `app/ingestion.py` — offline parsers, class/review filtering and canonical deduplication
- `app/rag.py`, `app/handoff.py` — evidence contract and response text; processing remains Day 2–3 work
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

**Security note:** The mock webhook token is only for local tests. Real Zalo OA/GMF access must be authorized and is out of the MVP scope. Deployment, real provider calls and all acceptance results require separate verification.

