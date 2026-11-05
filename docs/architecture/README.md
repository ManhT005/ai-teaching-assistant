# Architecture baseline — Day 1

- Decision: Lean Modular Monolith, FastAPI + PostgreSQL/pgvector.
- The only externally-facing channel is a simulated mock webhook for this MVP.
- Authorized class comes from a server-side channel binding; scope is filtered **inside retrieval SQL**.
- FAQ sections can have different scopes: `GS-KPI-01`, `AUTO-N8N-01`, `COMMON`.
- Document versions are staged and made active only after successful indexing.
- No LLM-generated answer may be claimed grounded until citation/evidence checks are implemented and tested.
- A persisted event is not a delivered answer; recovery/retry is a Day 4 deliverable.
- PostgreSQL `vector` column is dimensionless in this skeleton; select embedding provider and enforce its dimension **before Day 2 ingestion**.

The DOCX architecture overview is a separate handover artifact. Detailed design and ADRs remain in the user's Day 1 reference documents.

The agreed webhook/sample contracts, PDF/DOCX/XLSX fixtures and demo evidence policy are documented in [sample_contracts.md](sample_contracts.md). Offline parsing is now available; embedding, indexing and end-to-end handoff remain pending.

