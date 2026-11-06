# RAG ingestion contract

The index is **DEMO ONLY**. Supplied materials are simulated test data;
`provided_sample` never means approval of production policy.

Only an operator-controlled manifest grants scope and review eligibility. Source
text, worksheet cells and webhook payloads cannot grant access or approval.
Unknown sections, unsupported files and checksum mismatches fail closed.
The guide DOCX and event JSON are inventory items only, never index inputs.

The original bundle contains 8 GS sections, 10 AUTO sections and 8 FAQ sections.
The FAQ maps COMMON-01 through COMMON-04 to COMMON, GS-CLASS-01/02 to
GS-KPI-01, and AUTO-CLASS-01/02 to AUTO-N8N-01. Scope belongs to each chunk.
DOCX tables retain their block, table and row positions. Text PDFs retain pages;
scanned pages require OCR and are rejected. Worksheet locators retain row IDs.

`doc_id` identifies a logical source across formats. Manifest order chooses the
preferred representation of each section; alternate PDF/DOCX and derived FAQs
do not create additional evidence for that section. External `TEST-1.0` is stored
separately from the monotonically increasing internal database version.

Raw files and local manifests stay under ignored `data/raw/`. Model weights stay
under ignored `.models/`. Inventory reports contain IDs, counts and SHA-256 only.
No original course content, credentials or model weights belong in Git.

## Lifecycle

Parse, validate and chunk before staging. Pending sections never receive vectors.
An eligible section has review `provided_sample` or `approved_demo`; this tool
does not approve content. Synthetic material cannot claim `provided_sample`.

Embedding uses one pinned multilingual E5 revision, 384 dimensions, normalized
vectors and the model's passage/query prefixes. Token budgets include prefixes,
headings and special tokens. No model fallback or silent input truncation.

Staging, bounded embedding batches and activation use separate transactions.
An activation validates all staged chunks, then switches versions atomically.
A failed version stays inactive and the previous active version remains intact.
Revoke invalidates all versions immediately, including work currently staging;
re-ingesting identical revoked input cannot silently reactivate it.

The smoke SQL requires an active READY document, eligible document and chunk
review, the exact model revision and authorized class or COMMON before ordering
by cosine distance. It is an operator diagnostic, not a student retrieval API.

`approved_demo` requires an explicit human decision (`reviewer`, timezone-aware
`reviewed_at`, `reason`) in trusted metadata. The document version retains this
audit trail. The CLI cannot make a review decision. Pending remains pending.
Representation-specific filenames are stored on chunks as well as on documents;
a PDF page locator therefore still names the PDF when formats share a doc ID.

If a manifest removes a section or reduces its scope/review eligibility, existing
versions are revoked before embedding starts. An ordinary content update retains
the previous version on failure. A process crash can leave an inactive STAGING
version; the next ingest allocates a new version rather than exposing partial data.

References: [E5 model card](https://huggingface.co/intfloat/multilingual-e5-small),
[pgvector exact cosine search](https://github.com/pgvector/pgvector).
