# Sample contracts agreed on 2026-11-05

These decisions close the three sample/skeleton mismatches. They authorize mock data and derived fixtures for development. They do not constitute company approval of a newly authored teaching answer or acceptance of the full RAG MVP.

## Webhook and replay adapter

The public mock contract requires `event_id`, `group_id`, `sender_id`, `text` and timezone-aware `received_at`. `channel` defaults to `mock`; `is_bot` defaults to false. Extra fields, including `class_id`, are rejected. Original event time is separate from server intake time.

The webhook authenticates `X-Webhook-Token` before resolving the database dependency, resolves an active `(channel, group_id)` binding, checks class bot status and queues a question only when `/hoi` is a complete command with nonempty question text. Unknown or inactive groups are rejected before any retrieval. This token authorizes the local mock channel; it is not verification of a real Zalo identity or group membership.

| Local source class | Seeded mock group |
| --- | --- |
| GS-KPI-01 | mock-google-sheets |
| AUTO-N8N-01 | mock-ai-automation |

The converter uses the source fixture's `class_id` only to choose one of those local groups. Runtime `class_id` comes exclusively from `ChannelBinding`. Keep the mapping and seed together in `app/mock_channel.py`; changes to the fixture cannot alter server authorization. Unknown source classes fail conversion. `event_id`, sender, original text and event time remain recoverable from the unchanged input. `--add-trigger` prepends `/hoi` only where needed; the default preserves raw text for negative tests.

The server stores one event per `(channel, event_id)` using PostgreSQL's unique constraint. Duplicate intake does not imply exactly-once delivery to an external messaging platform. Different payloads reusing an ID do not create another event. Delivery recovery, rate limits and real channel authentication remain separate milestones.

## Knowledge fixture formats and provenance

| Asset | Source and purpose |
| --- | --- |
| automation_excerpt.docx | Derived excerpt of DOC-AUTO-01 TEST-1.0, AUTO-03 and AUTO-05 |
| automation_excerpt.pdf | Same two sections in a text-based PDF; alternate parser fixture |
| knowledge_demo.xlsx | Sheet `knowledge`, 7 supplied-source records and 1 synthetic pending draft |
| knowledge_records.json | Readable source of the workbook records; update together with XLSX |
| manifest.json | Source-bundle checksum, original filenames, section scopes and canonical preference |

The original ZIP and full supplied course files are not committed. Derived files retain the original statement that their content is simulated. Source metadata uses the external version label `TEST-1.0`; mapping that label into the database's integer version belongs to the indexing milestone.

The XLSX header is row 1, in this exact order:

`record_id`, `class_scope`, `question`, `answer`, `source_doc_id`, `source_section_id`, `version`, `data_origin`, `review_status`, `topics`.

Each row has a stable ID, source and section reference. Excel row numbers supplement the ID in citations. The parser validates required fields, scope/status enums and unique IDs; formulas are rejected rather than evaluated. Currently each source section has one FAQ record; split genuine source subitems explicitly when adding multiple distinct answers from one section.

Assign scopes per section/row, including in a mixed FAQ file. Only general rules use `COMMON`. Google Sheets and Automation schedules/deadlines remain in their respective classes. Scope in a curated local fixture is operator-controlled. These helpers are not an upload authorization endpoint; do not allow unreviewed user uploads to assign their own scope or approval.

Demo eligibility permits supplied simulated content (`provided_sample`) and explicitly reviewed demo additions (`approved_demo`); `pending` is excluded. `data_origin=synthetic_demo` can never claim `review_status=provided_sample`. These labels do not authorize production use.

The canonical key is `(doc_id, section_id, version, class_scope)`. First representation wins, with DOCX first in the manifest. The PDF is parser coverage rather than independent corroboration. Hashes and source identity must not be counted as evidence that a teaching answer is correct.

## RAG versus Fine-tuning

AUTO-05 supports an explanation of RAG, but does not support the full comparison. `DEMO-AUTO-003` is a synthetic comparison draft, currently `pending`, scoped only to `AUTO-N8N-01`. It is not eligible evidence. The mandatory comparison scenario therefore remains incomplete; the expected safe fallback is handoff.

Before using that draft: review its content, record the actual reviewer/decision in a tracked review change, update JSON and XLSX consistently, and assign `approved_demo` only after that decision. Production approval remains separate. No approval is fabricated by this change.

The demo gate requires authorized reviewed sources, complete required topic coverage and a separate verified relevance result. Its default is handoff. Topic tags are curated test metadata, not a semantic assessment; retrieval relevance, answer validation and citations still need implementation. Tests exercise a hypothetical approval only in memory and leave committed data pending.

## Verification and remaining work

Offline tests cover conversion of all 8 supplied events, raw-message behavior, strict schema, auth before database access, all three parsers, source locators, scope separation, duplicate representations and pending/partial-evidence fallback.

PostgreSQL tests cover persisted class mapping and event time, duplicate intake, unknown/inactive groups, injected `class_id`, disabled bots and ignored messages. CI provisions pgvector, applies both migrations, repeats seed and runs the complete suite. Local runs skip database tests unless `TEST_DATABASE_URL` is set to a dedicated migrated test database.

No completed embedding/indexing, SQL retrieval isolation, LLM answer, delivered reply or persisted teacher ticket is claimed here. The webhook still returns `accepted` for queued work. The parser script reports an evidence decision offline; the processing worker must wire it to a persisted handoff before that end-to-end scenario can pass.
