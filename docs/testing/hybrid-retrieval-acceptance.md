# Hybrid retrieval acceptance

Executed on **2026-11-06**, Asia/Saigon, on `feature/rag-retrieval`, based on
`origin/develop` commit `5e312b7` after ingestion PR #2 merged.

Implementation and local verification: **PASS**. Hosted CI and independent human
qrels review have not been performed. The measured hybrid ranking meets the
provisional recall gates but is slightly worse than dense-only in aggregate.

## Environment and reproduction

- Windows, Python 3.11.9, project `.venv`, cached E5 model on CPU.
- Pinned embedding identity:
  `intfloat/multilingual-e5-small@614241f622f53c4eeff9890bdc4f31cfecc418b3`,
  384 dimensions, normalized vectors, query/passages prefixes unchanged.
- PostgreSQL 17.11, pgvector 0.8.7, dedicated `gos_smoke_test` database on
  loopback port 55417. No production database was used.
- Three active original-source documents, 26 sections, 27 existing vectors.
  Migration `0005` applied without re-ingestion; original vectors and version 1
  activation remained intact. Disposable schemas isolate all integration tests.
- Candidate budgets: dense 10, lexical 10, maximum technical tokens 6, RRF k=60,
  final top-k=3. No query-specific boosts or post-benchmark tuning.

After setting `DATABASE_URL` and `TEST_DATABASE_URL` to the dedicated test DB:

```powershell
python -m alembic upgrade head
python -m scripts.hybrid_search --group mock-ai-automation --query "Em chạy n8n bị lỗi 401 Unauthorized"
python -m scripts.evaluate_retrieval --check-target --output .pytest_cache/hybrid-evaluation.json

$env:ORIGINAL_SOURCES_MANIFEST = 'data/raw/manifest.local.json'
$env:RUN_LIVE_EMBEDDING = '1'
python -m pytest -q --basetemp=.pytest_cache/hybrid-acceptance --junitxml=.pytest_cache/hybrid-acceptance.xml
```

The full suite returned **129 passed, 0 failures, 0 errors, 0 skipped**, in 32.56
seconds. This includes all Day 1 and ingestion regression tests. Original-source
and real-model tests were explicitly enabled locally. CI runs deterministic
retrieval tests and real PostgreSQL integration, without downloading E5 weights.

The CI workflow now retains JUnit results as an artifact. Its hosted execution
has not run for these local commits. Changed Python files pass Ruff checks and
formatting at the repository's 100-character line length. No raw source files,
model weights or credentials were committed.

## Frozen relevance dataset

[Qrels v1.0](../../data/samples/retrieval_qrels.json) contains **30 questions**:
26 answerable and four no-evidence cases. The implementation author read all 26
source sections and assigned section relevance before the first measurement.
The file identifies this as automated Codex source review; no human or company
approval is claimed. It is a small developer regression set, not an independent
held-out quality benchmark.

Qrels SHA-256:
`8d3c6719cc51fb2c189469c899f72960fb1ff2daa509c12a7fbd6fae447fc253`.
Expected section labels and source checksums were unchanged across both local
quality measurements; the resulting relevance metrics were identical.

Evaluation aborts if the active corpus has missing/additional logical documents,
source checksums differ, or a labeled relevant section is missing/unauthorized.
The [sanitized machine-readable evidence](hybrid-retrieval-benchmark.json)
contains per-case ranks, true chunk IDs, versions, locators, timings and hashes.
It contains no source passages or secrets.

## Measured quality

Measured at `2026-11-06T16:10:06Z` / 23:10 Asia/Saigon. Metrics are macro-averaged
over the 26 answerable cases; top-3 refers to the first three chunk rows. Relevant
section IDs count once even if several chunks from that section appear.

| Method | Hit@3 | Recall@3 | MRR@3 |
| --- | ---: | ---: | ---: |
| Dense only | 100.00% | 95.51% | 1.0000 |
| Lexical only | 73.08% | 69.87% | 0.6859 |
| Hybrid RRF | 100.00% | 93.59% | 0.9808 |

All returned candidates in both legs and final hits respected their authorized
class/COMMON scope: **0 cross-class hits**, including negative questions asking
about the other class's technical material.

The provisional hybrid Recall@3 ≥90% gate passes. The implementation uses an
explicit two-percentage-point absolute recall tolerance to operationalize the
plan's "no material regression"; observed regression is **1.92 percentage
points**, so that local gate also passes. This tolerance is a developer choice,
not a GOSTECH acceptance requirement. With a strict zero-regression gate, the
comparison would fail. This result does not establish that hybrid improves
overall quality.

Representative results:

| Case | Dense top-3 | Hybrid top-3 |
| --- | --- | --- |
| n8n 401 (AUTO-01) | AUTO-09, AUTO-03, AUTO-10 | AUTO-03, AUTO-09, AUTO-CLASS-01 |
| IMPORTRANGE notes/comments (GS-02) | GS-04, GS-CLASS-02, GS-05 | GS-04, GS-CLASS-02, GS-05 |
| Mask support screenshots (COMMON-01, GS scope) | COMMON-02, COMMON-01, GS-04 | COMMON-02, COMMON-01, GS-04 |
| Teacher response time (COMMON-02, AUTO scope) | COMMON-01, COMMON-04, AUTO-10 | COMMON-01, COMMON-04, AUTO-10 |

The operator CLI separately succeeded for the 401 query. Its first hit was the
real chunk row 12 / AUTO-03, internal version 1 / TEST-1.0, source
`02_Giao_trinh_AI_Automation_n8n.docx`, blocks 9–13. Dense rank 2, lexical rank 1,
RRF score `0.03252247488101534`. That score is a ranking diagnostic, not confidence.

## Miss and regression analysis

No answerable question lost all relevant evidence from top-3. Four questions have
incomplete multi-section recall:

| Case | Missing relevant section | Observation |
| --- | --- | --- |
| GS-08: cancelled revenue | GS-CLASS-02 | No technical lexical anchors; dense fallback returns GS-01 and GS-06, missing the redundant FAQ source. Same miss in dense. |
| GS-09: GS deadline | GS-CLASS-02 | Dense fallback finds the main class schedule but not the FAQ repetition. Same miss in dense. |
| AUTO-01: n8n 401 | AUTO-CLASS-02 | Hybrid promotes the principal technical source to rank 1, but broad `n8n` also ranks the class overview. Both methods recall two of three labeled sections. |
| AUTO-12: AUTO deadline | AUTO-CLASS-02 | Hybrid recalls only the main schedule. The lexical `n8n` anchor rewards general technical material; dense recalls both schedule sources. This is the additional hybrid recall regression. |

AUTO-11, the question about class isolation, keeps AUTO-06 in top-3 but moves it
from dense rank 1 to hybrid rank 2; this causes the MRR reduction. The lexical
`rag` anchor rewards a more general RAG section. These are reasons for future
held-out evaluation of candidate budgets/weighting or lexical selection, not
reasons to edit labels or add special-case rank rules to this PR.

Four no-evidence cases are explicitly excluded from relevance averages: an AUTO
question about GS formulas, a GS question about n8n, actual commercial prices and
the unsupported RAG/fine-tuning comparison. Similar allowed-scope hits still
appear, as expected for nearest-neighbor retrieval. A future Evidence Gate must
withhold unsupported answers; this service does not make that decision.

## Observed latency

One warmed serial sample per question, 30 samples. The initial 11.12-second model
warmup is recorded separately. SQL-leg measurements include fetching/validating
candidate rows; timings describe the search core, not HTTP or answer generation.
This is not a concurrency or production load benchmark.

| Stage | p50 ms | p95 ms |
| --- | ---: | ---: |
| E5 encode | 17.61 | 26.63 |
| Scope/connection | 7.08 | 8.44 |
| Dense SQL leg | 4.78 | 6.37 |
| Lexical SQL leg | 3.34 | 5.18 |
| Rank fusion | 0.13 | 0.23 |
| Search core total | 35.15 | 56.27 |

## Plan acceptance matrix

| Plan IDs | Result | Evidence |
| --- | --- | --- |
| HYB-01–03 | PASS | Real E5 benchmark retains AUTO-03, GS-04 and GS-02 in top-3 |
| HYB-04–05 | PASS | Both SQL legs share scope predicates before LIMIT; cross-class fixtures and real negative cases |
| HYB-06 | PASS | Shared COMMON sources available from both database bindings |
| HYB-07 | PASS | Exact-keyword synthetic pending chunks excluded from both legs |
| HYB-08–09 | PASS | Revoke, updated version, inactive rows and repeatable snapshot tests |
| HYB-10–11 | PASS | Vietnamese filler extraction, positive OR syntax, hostile operators and SQL-looking end-to-end query |
| HYB-12–13 | PASS | Invalid query/top-k rejection; no terms and zero lexical matches retain scoped dense fallback |
| HYB-14 | PASS | Exact RRF scores, missing legs, duplicate rows and ID tie-breaks |
| HYB-15 | PASS | Fresh/populated migrations, generated content update, downgrade/repeated upgrade preserve vectors and active versions |
| HYB-16 | PASS | Repeated results/citations/ranks; read-only transactions, no writes or connection during embedding |
| HYB-17 | PASS | Twelve concurrent searches over two contexts using four workers, no context bleed |
| HYB-18 | PASS | Full 129-test local suite, including originals and live E5 |
| Hosted CI | NOT RUN | Branch is local; workflow updated to retain its future JUnit artifact |
| Independent human qrels review | NOT PERFORMED | Automated implementation-author source review is explicitly recorded |

## Delivery scope

Seven commits correspond to RET-H01 through RET-H07. The delivered service and
operator CLI return source candidates only. Student search API, LLM generation,
Evidence Gate, tickets, webhook workers and real Zalo remain outside this change.
The model and ingestion parser/lifecycle were not rewritten. PostgreSQL `simple`
is not full Vietnamese NLP, and no accent-insensitive retrieval is claimed.

Commits remain local on `feature/rag-retrieval`; no push, PR creation or merge was
performed. A future PR must target `develop` and retain the quality limitations,
pending hosted CI and qrels review status above.
