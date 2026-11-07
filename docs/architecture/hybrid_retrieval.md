# ADR-RET-01: scoped dense + PostgreSQL FTS + RRF

Decision: keep retrieval in the existing Python monolith. Use exact pgvector
cosine candidates and built-in PostgreSQL full-text candidates, then fuse their
rank positions with reciprocal rank fusion. No external vector store, reranker,
query-expansion LLM or orchestration framework is introduced.

## Data flow and identity

1. Trusted application code authenticates its channel upstream. An operator CLI
   instead runs with direct database privileges; it is not a student-facing API.
2. `resolve_authorized_scope` reads an enabled `ChannelBinding` and classroom.
   The returned immutable `AuthorizedScope` carries an internal issuer marker.
   A plain class string/dictionary cannot authorize the service. Bindings are
   revalidated on every search, rejecting disabled, rebound and forged contexts.
3. Normalize Unicode to NFC, collapse whitespace, preserve formula punctuation,
   reject controls/blank input and more than 1000 characters. The encoder also
   rejects questions exceeding E5's 512-token budget instead of truncating them.
4. Encode once using the same pinned, normalized E5 query representation used by
   ingestion. No database connection is held during encoding.
5. Start a read-only REPEATABLE READ transaction. Revalidate the binding and run
   both candidate legs in that snapshot using the shared SQL allowlist.
6. Validate metadata agreement for rows present in both legs, fuse ranks and
   return top-k typed candidates and citations. No answers or tickets are created.

Python's issuer marker protects the internal calling contract; it is not a
credential or a replacement for authenticating a sender. A group name and the
mock webhook shared secret cannot prove real student membership. No HTTP search
endpoint was added. A future endpoint must resolve membership through a verified
server-side identity/binding flow before issuing this context.

## One SQL allowlist

`eligible_chunk_predicates` is used by `scoped_candidate_query` for both legs and
by the refactored legacy dense smoke command:

- Document is active, READY and has no revocation timestamp.
- Document and chunk reviews are `provided_sample` or `approved_demo`.
- Document model is the pinned E5 revision, dimension 384.
- Chunk scope is the authorized class or COMMON, and embedding is nonnull.

These predicates are inside each query before ranking and LIMIT. There is no
global retrieval followed by a Python permission filter. SQLAlchemy binds query
text and lexical tsquery strings as values.

The read-only snapshot prevents one request from mixing pre-reindex dense rows
with post-reindex lexical rows. Revocation committed before a new search snapshot
is excluded from both legs. A search already in flight retains its consistent
earlier snapshot; the next request observes the revocation. There is no result or
authorization cache to invalidate.

## PostgreSQL FTS

Migration `0005_hybrid_retrieval` adds a stored generated `search_vector`:

```sql
to_tsvector('simple'::regconfig, coalesce(content, ''))
```

Its GIN index is maintained by PostgreSQL. Existing rows become searchable and
new ingestion rows need no special write path. Downgrade drops only the GIN
index and generated column, preserving content, embeddings and active versions.
Migrations `0001` through `0004` are unchanged.

The conservative extractor recognizes technical anchors from a fixed vocabulary
plus three-digit HTTP codes 100–599. It keeps input order, deduplicates and caps
terms at six by default. For example, `Em chạy n8n bị lỗi 401 Unauthorized` becomes
`n8n | 401 | unauthorized`. Every token passes an ASCII lexeme validator before
joining with positive OR operators; user-provided tsquery operators, quotes,
wildcards and SQL-looking punctuation are never copied into query syntax.

Full Vietnamese filler sentences are not turned into a restrictive AND query.
`simple` recognizes the tested formulas/status codes but is not a Vietnamese
segmenter and does not strip accents. The extractor does not attempt synonyms,
accent folding or arbitrary natural-language keyword discovery. Unknown/no-match
terms leave the lexical branch empty and preserve scoped dense fallback.

## Ranking and response

Dense candidates sort by `(cosine_distance ASC, row_id ASC)`. Lexical candidates
sort by `(ts_rank_cd DESC, row_id ASC)`. Each branch defaults to 10 candidates and
is configurable from 5 to 50. Dense distances and lexical scores are diagnostics;
they are not added together.

For each unique database chunk row:

```text
rrf_score = sum(1 / (60 + branch_rank))
```

Ranks start at one; a missing branch contributes zero. Equal scores sort by row
ID. Duplicate rows contribute once per leg. Deduplication uses chunk row IDs,
not filenames/documents/sections: multiple chunks in one section are legitimate.

Each `RetrievalHit` contains its real stable chunk hash, database chunk/document
IDs, logical document ID, internal/external versions, section, source filename,
locator, class scope, content, branch ranks/scores and RRF score. Diagnostics
contain bounded branch IDs/sections and timings, without source passages. CLI
content printing requires `--include-content`; normal logs contain only binding
ID, counts and duration.

RRF is a ranking score, not a probability or evidence sufficiency threshold. The
future Evidence Gate must decide whether any returned material supports an answer.

## Quality method and limits

The frozen qrels contain 30 source-checked questions: 26 answerable and four
no-evidence cases. The implementation author checked labels against the supplied
simulated originals before measurement; this is explicitly automated review,
not independent human review or company approval. The qrels' source checksums
must match exactly the active corpus. Missing/unauthorized relevant sections and
unexpected active documents abort the evaluator.

All branches use the same scope and top-3 boundary. Recall@3 is macro-averaged
section recall over answerable questions; duplicate chunks do not count a relevant
section twice. MRR uses the first relevant chunk's original rank. No-evidence
cases are recorded separately and cannot inflate relevance averages; similar
retrieval hits are expected and must not be interpreted as answer permission.

The provisional local gate is hybrid Recall@3 ≥90% with no more than two absolute
percentage points of Recall@3 regression versus dense-only. This tolerance is an
explicit developer interpretation of the plan's "no material regression", not a
GOSTECH requirement. `--check-target` exits 2 if either condition fails. Candidate
budgets, equal fusion weights and frozen labels were not tuned after measuring.

The first comparison meets those provisional gates, but hybrid trails dense by
1.92 percentage points and harms one schedule query. Misses and latency samples
are retained in the [acceptance report](../testing/hybrid-retrieval-acceptance.md).
Independent label review, a larger held-out benchmark and systematic tuning remain
needed before claiming a general quality improvement.

References: [PostgreSQL text-search controls](https://www.postgresql.org/docs/17/textsearch-controls.html),
[stored generated text-search vectors](https://www.postgresql.org/docs/17/textsearch-tables.html),
[GIN indexes](https://www.postgresql.org/docs/17/textsearch-indexes.html),
[pgvector hybrid search](https://github.com/pgvector/pgvector#hybrid-search).
