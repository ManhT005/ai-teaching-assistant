# Local PostgreSQL 17 smoke test

Result: **PASS**, executed on 2026-11-05 (Asia/Saigon).

Application commit: `6708a1e63b4e0ff301d9e45d31f0a667da90f572`.
No application changes were required.

## Environment

- Windows, Python 3.11.9 in the project-local `.venv`.
- Docker image `pgvector/pgvector:pg17`, Linux amd64.
- Image ID: `sha256:7d0a41464797aeb9862dfdc0bbf1526b82cfd324cdd8036df19e3545f4a9ccb8`.
- Actual server: PostgreSQL `17.11 (Debian 17.11-1.pgdg12+2)`.
- Installed database extension: pgvector `0.8.7`.
- Dedicated database `gos_smoke_test`, user `gos_smoke`, port `127.0.0.1:55417`.
- Isolated Compose project: `ai-teaching-smoke-pg17`.
- HTTP checks used Uvicorn at `127.0.0.1:58017` with real database sessions.
- Relevant dependencies: FastAPI 0.162.0, Starlette 1.10.0, SQLAlchemy 2.1.8,
  psycopg 3.3.8, pgvector Python client 0.5.1, Alembic 1.20.1,
  pytest 9.2.0, httpx 0.28.1, Uvicorn 0.54.0.
- `python -m pip check`: no broken requirements.

Credentials were generated locally and kept in ignored `.venv/smoke/` files.
The repository `.env` was not created or modified.

## Results

| Check | Observed result |
| --- | --- |
| Server preflight | Server version number is in the PostgreSQL 17 range; correct test database |
| Fresh migration | `0001_initial` and `0002_source_received_at` applied successfully |
| Repeated migration | A second `alembic upgrade head` succeeds |
| Schema revision | `0002_source_received_at` |
| Vector operation | `'[1,2,3]'::vector <-> '[4,6,3]'::vector` returns `5.0` |
| Repeated seed | Two seed runs produce identical rows: 2 classes, 2 bindings |
| Offline material check | 12 parsed chunks, 9 logical chunks; comparison question yields `handoff / INCOMPLETE_EVIDENCE` |
| Complete pytest suite | **31 passed, 0 failed, 0 errors, 0 skipped**, 2.23 seconds |
| PostgreSQL integration | All 7 database test cases executed; transactions rolled back |
| HTTP health/readiness | `/health` and `/ready` return 200; readiness confirms vector extension |
| API documentation | `/docs` and `/openapi.json` return 200 |
| Webhook authentication | Missing and incorrect token return 401 |
| Unknown group | Returns 403 |
| Client-supplied class / naive timestamp | Each returns 422 |
| Ignored messages | Ordinary chat, `/hoiday`, empty `/hoi`, bot message return `ignored`; no stored event |
| Event intake | First authorized request returns 202 `accepted` |
| Event replay | Sequential replay and 8 concurrent replays each return 200 `duplicate` |
| Persisted event | Exactly 1 row, class `AUTO-N8N-01`, status `PENDING`, source timestamp preserved as UTC, server intake timestamp populated |
| Future processing tables | No answers, tickets or model usage created |
| Cleanup | HTTP event deleted; final database has 2 classes, 2 bindings, 0 events, 0 answers, 0 tickets |

The pytest suite additionally verifies both demo class mappings, disabled bots,
inactive bindings, parsing, sample conversion, and evidence rules.

One dependency warning was emitted: Starlette deprecates `httpx` usage in its
`TestClient` in favor of `httpx2`. It did not fail any test.

## Local artifacts and repeat run

The local helper, Compose configuration, credentials and logs remain in the
git-ignored `.venv/smoke/` directory. They are local execution artifacts and
are not part of the committed project interface.

- `run.py`: local smoke driver; configures `DATABASE_URL` and
  `TEST_DATABASE_URL` for the dedicated test database and a random webhook token.
- `results.json`, `pytest.xml`, `pytest.log`: detailed results.
- `migration.log`, `migration_repeat.log`, `seed_first.log`, `seed_repeat.log`,
  `materials.log`, `uvicorn.log`: individual command logs.

To repeat on this workspace while those local artifacts exist:

```powershell
docker compose -p ai-teaching-smoke-pg17 --env-file .venv/smoke/.env -f .venv/smoke/compose.yaml up -d --wait --wait-timeout 120
.\.venv\Scripts\python.exe .venv/smoke/run.py
docker compose -p ai-teaching-smoke-pg17 --env-file .venv/smoke/.env -f .venv/smoke/compose.yaml stop db
```

The Uvicorn process and dedicated database container were stopped after testing.
The database container and its test data remain available for a repeat run.

This validates the currently implemented skeleton and event intake. Embeddings,
retrieval, LLM answers, persisted handoff and real Zalo integration are outside
this smoke test because those features are not implemented yet. The existing
GitHub Actions workflow still targets PostgreSQL 16; this report records the
separate local PostgreSQL 17 execution.
