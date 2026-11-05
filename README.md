# GOS Academy — AI Teaching Assistant

Lean MVP for a teaching assistant using **FastAPI + PostgreSQL/pgvector + a simulated Zalo webhook**.

> **Status:** Day 1 repository skeleton. The health API, database schema, class seed and authenticated mock-webhook event intake are implemented. RAG, document ingestion, grounded responses, teacher handoff, retries and reporting are **not implemented yet**. No real Zalo integration.

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
} | ConvertTo-Json

Invoke-RestMethod -Method Post -Uri http://127.0.0.1:8000/webhook/mock `
  -Headers @{"X-Webhook-Token" = "<your-local-token>"} `
  -ContentType "application/json" -Body $body
```

Expected: first event `accepted`, replay of the same `(channel,event_id)` `duplicate`. This **only stores the event**; no AI answer is generated yet. Messages without `/hoi` are ignored.

## Project map

- `app/main.py` — API entrypoint
- `app/api/` — health, readiness and mock webhook
- `app/models.py` — class, channel mapping, documents, chunks, events, answers, tickets and usage
- `app/db.py` — one SQLAlchemy connection/session setup
- `app/rag.py`, `app/ingestion.py`, `app/handoff.py` — explicit Day 2–3 extension points
- `alembic/` — versioned database migration, including pgvector extension
- `scripts/seed.py` — seed two demo classes and mock group bindings
- `tests/` — offline structural tests
- `docs/architecture/` — design notes
- `data/samples/` — no confidential training documents committed

## Roadmap

| Day | Deliverable |
| --- | --- |
| 1 | Skeleton, PostgreSQL/pgvector, migration, seed, webhook intake |
| 2 | Approved document ingestion and isolated retrieval by class |
| 3 | Grounded LLM answer, citations, insufficient-evidence handoff |
| 4 | Webhook processing/recovery, controls, metrics |
| 5 | Mandatory scenarios, report, demo and handover |

**Security note:** The mock webhook token is only for local tests. Real Zalo OA/GMF access must be authorized and is out of the MVP scope. Deployment, real provider calls and all acceptance results require separate verification.
