# RAG ingestion acceptance

Executed locally on **2026-11-06**, Asia/Saigon. Result: **PASS** for the ingestion
scope. This does not certify a complete RAG assistant or production policies.

## Environment and execution

- Windows, project-local Python 3.11.9.
- Dedicated test database `gos_smoke_test`, loopback port 55417, isolated Compose
  project `ai-teaching-smoke-pg17`. No application production database was used.
- PostgreSQL `17.11 (Debian 17.11-1.pgdg12+2)`, pgvector `0.8.7`.
- E5 CPU: `intfloat/multilingual-e5-small`, pinned revision
  `614241f622f53c4eeff9890bdc4f31cfecc418b3`, normalized 384-dimensional vectors.
- sentence-transformers 5.7.0, transformers 5.22.1, torch 2.15.0, sentencepiece
  0.2.3. Model weights were downloaded once, then used from ignored `.models/`.
- Database integration creates disposable schemas inside the dedicated test DB.
  Each schema is dropped after its test; the demo index remains available.
- Model and original-source acceptance were enabled explicitly. CI does not
  download model weights; its PostgreSQL service now uses version 17.

Local test invocation, after setting `TEST_DATABASE_URL` to that isolated DB:

```powershell
$env:ORIGINAL_SOURCES_MANIFEST = 'data/raw/manifest.local.json'
$env:RUN_LIVE_EMBEDDING = '1'
python -m pytest -q --basetemp=.pytest_cache/acceptance-final --junitxml=.pytest_cache/rag-acceptance.xml
```

The complete suite passed with **66 passed, 0 failed, 0 errors, 0 skipped**.
Day 1 webhook/health/schema/material regression tests were included. Ruff import,
syntax and unused-name checks passed for all changed Python files; those files
were formatted at a 100-character line length. `pip check` found no broken
requirements. The hosted GitHub Actions workflow was updated but was not run
remotely in this session.

## Original source inventory

No original files were changed. Inventory and manifest generation used
`python -m scripts.inspect_sources --write-manifest data/raw/manifest.local.json`.

| Original | Paragraphs | Tables | Sections | Indexed |
| --- | ---: | ---: | ---: | --- |
| `00_Huong_dan_su_dung_bo_hoc_lieu_mau.docx` | 24 | 1 | 0 | No; internal guide |
| `01_Giao_trinh_Google_Sheets_KPI_Dashboard.docx` | 38 | 1 | 8 | Yes; GS scope |
| `02_Giao_trinh_AI_Automation_n8n.docx` | 33 | 0 | 10 | Yes; AUTO scope |
| `03_FAQ_Quy_dinh_lop_hoc_GIA_LAP.docx` | 23 | 2 | 8 | Yes; section-specific scope |
| `Tin_nhan_mo_phong_TTS.json` | — | — | — | No; webhook fixture only |

SHA-256 of the indexed originals:

| Logical document | SHA-256 |
| --- | --- |
| DOC-GS-01 | `59fd19dd401a2ad65960f536016420106edd96f239b28d06833d31a94292590e` |
| DOC-AUTO-01 | `8a8078ad32825192b5f8f4b3478ca870394ce67a20409798b4e22ad1b1f42bcc` |
| DOC-FAQ-01 | `88b92d7c1c9b95946abcdd6e3dd21065d9962b7d0a392689a86e2b61da0928d7` |

`git ls-files data/raw .models` returned no files. `git check-ignore` confirmed
that originals, the local manifest and model cache remain ignored. No credentials,
raw course text or model weights are present in the committed evidence.

## Live ingestion and smoke transcript

The [sanitized JSON transcript](rag-ingestion-smoke.json) contains counts,
checksums, model identity and hits with source locators. It contains no source
passages. Reproduce with the ingest/smoke commands in the README.

| Check | Observed result |
| --- | --- |
| Parse all originals | 26 sections; GS-02/04, AUTO-03/05, COMMON-01/03 and class-specific FAQ sections retained |
| Chunk/tokenizer | 27 chunks; maximum 314 input tokens, including passage prefix and special tokens |
| Scope counts | GS-KPI-01: 11; AUTO-N8N-01: 12; COMMON: 4 |
| First ingest | Three READY active documents at internal version 1, external label TEST-1.0 |
| Chunks by document | DOC-GS-01: 9; DOC-AUTO-01: 10; DOC-FAQ-01: 8 |
| Stored real vectors | 27, all 384 dimensions, one pinned model revision |
| Repeated ingest | All three documents skipped; counts remain unchanged |
| n8n 401 query | AUTO-03 appears at rank 2 with block locator |
| GS IMPORTRANGE query | GS-04 appears at rank 1 with block locator |
| IMPORTRANGE query in AUTO class | Only AUTO/COMMON scopes are eligible; no GS sources returned |

For `Em chạy n8n bị lỗi 401 Unauthorized`, authorized class AUTO-N8N-01:

| Rank | Section | Cosine distance | Locator |
| ---: | --- | ---: | --- |
| 1 | AUTO-09 | 0.124004 | block 30; block 31 |
| 2 | AUTO-03 | 0.135230 | block 9; block 10; block 11; block 12; block 13 |
| 3 | AUTO-10 | 0.138406 | block 32; block 33 |

For `IMPORTRANGE có đồng bộ ghi chú và bình luận không?`, authorized class GS-KPI-01:

| Rank | Section | Cosine distance | Locator |
| ---: | --- | ---: | --- |
| 1 | GS-04 | 0.109767 | block 22; block 23; block 24 |
| 2 | GS-CLASS-02 | 0.140850 | block 16; block 17 / table 1 / rows 1–5 |
| 3 | GS-05 | 0.186992 | block 25; block 26 |

The same IMPORTRANGE query under AUTO-N8N-01 returned AUTO-05, AUTO-06 and AUTO-07.
These ranks demonstrate storage and initial semantic retrieval only. They are not
an evidence threshold, answer-quality benchmark or permission to generate an
answer about GS content for an AUTO student.

## Acceptance matrix

| Plan tests | Result | Evidence |
| --- | --- | --- |
| ING-T01–03 | PASS | Original-source tests: all 26 sections, table/formula and mixed-scope FAQ checks |
| ING-T04 | PASS | Inventory whitelist rejects JSON; guide excluded |
| ING-T05–06 | PASS | PDF/XLSX fixture regression, page/row locators and canonical DOCX/PDF dedup |
| ING-T07 | PASS | Pending ingest produces no active version; SQL excludes pending even with an exact vector match |
| ING-T08 | PASS | Deterministic boundary tests and real E5 tokenizer with long Vietnamese/formula text |
| ING-T09 | PASS | Real Vietnamese passage/query vectors, validated shape/norm and stored in pgvector |
| ING-T10–11 | PASS | No-op re-ingest, forced rebuild and new internal version under unchanged external label |
| ING-T12 | PASS | Failure after first embedding batch, corrupt staging rejection and DB-triggered activation rollback preserve old active version |
| ING-T13 | PASS | Immediate revoke blocks hits, in-flight activation and identical revoked re-ingest |
| ING-T14 | PASS | SQL filtering tests and real cross-class smoke; COMMON remains allowed |
| ING-T15 | PASS | AUTO-03 at rank 2 in real top-3 |
| ING-T16 | PASS | Wrong model, wrong dimensions, NaN/Inf/zero/non-normalized vectors rejected |
| ING-T17 | PASS | Fresh migration, Day 1 upgrade, incompatible legacy vector rollback and complete regression |
| ING-T18 | PASS | Git ignore and tracked-file checks for originals/model cache |

## Delivery and limits

Implementation commits follow ING-00 through ING-07. One merge commit incorporates
the existing remote ingestion branch/develop history. Migration `0004` supplements
`0003` with representation-specific source filenames and a retained human review
audit; migrations `0001` and `0002` are unchanged.

All production code uses the real pinned model; fake embeddings exist only in
deterministic tests. The synthetic comparison FAQ stays pending. This run did not
approve policy content or modify source documents. Review/scope reductions revoke
older versions immediately, even if a subsequent embedding fails.

The delivered functionality is ingestion plus an operator SQL smoke command.
Student retrieval API, generation, evidence thresholds, teacher tickets,
webhook workers, hybrid/HNSW search and real Zalo remain outside this change.
Image/scanned PDFs require OCR and are explicitly unsupported.

Commits are local on `feature/rag-ingestion`. No remote push, hosted CI run, PR
creation or merge was performed. A future PR must target `develop` and describe
this ingestion scope and the above limits.
