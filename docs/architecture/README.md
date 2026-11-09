# Architecture

The application is a modular monolith using FastAPI, SQLAlchemy and PostgreSQL 17
with pgvector. The mock webhook authenticates the channel, resolves the group
binding and stores events before acknowledging them.

| Package | Responsibility |
| --- | --- |
| `app/api` | Health, readiness and authenticated mock intake |
| `app/knowledge` | Source metadata, parsing, chunks, pinned E5 vectors and document lifecycle |
| `app/retrieval` | Server-issued scope, dense/FTS candidates and reciprocal rank fusion |
| `app/generation` | Evidence policy, strict claim contracts, safety and validation |
| `app/llm` | Provider protocol, fake, Gemini and separated system/data prompts |
| `app/events` | Atomic claim, finalization, tickets, usage and operator response text |

Shared database models, settings and session setup remain at the package root.
Scripts are operator entrypoints; there is no public class-selectable ask API.
The webhook ACK means stored, not answered or delivered.

Detailed contracts:

- [Sample and channel contracts](sample_contracts.md)
- [Ingestion lifecycle and fixed embedding contract](ingestion_contract.md)
- [Scoped hybrid retrieval](hybrid_retrieval.md)
- [Grounded generation and handoff](grounded_generation.md)
- [Event reliability and mock delivery](event_reliability.md)

Only eligible reviewed active sources can be retrieved. The processor rechecks
the binding and source snapshot before committing an answer. Network generation
holds no database connection. Event locks and database guards ensure one internal
Answer or Ticket outcome; recovery, bounded retries and delivery belong to Day 4.

