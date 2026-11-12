# ADR-GEN-02: bounded quote selection and composition

The model selects 1–3 request-local quote IDs. It never supplies answer prose,
claims, citation metadata or chunk IDs. `AnswerSelection` is strict and forbids
extra fields. An explicit handoff has an empty ID list. Invalid, repeated or
unknown IDs reject the entire result, without retaining a valid subset.

The server creates q001…q008 from the accepted EvidenceDecision in retrieval-hit
order, then original paragraph order. Identical text from different chunks keeps
separate IDs and provenance. Conflicting identities, missing source hits and
modified/partial paragraphs fail closed. Provider input is a canonical copy;
the same catalog numbering is reproduced by the validator.

Composition copies complete original paragraphs into at most three single-source
claims, preserving selection order and negation. Joined answer text is bounded at
6,000 characters; no quote is truncated. Temporary quote IDs never replace stable
chunk provenance in the database. Citation metadata is deduplicated by chunk in
first-selected order. The existing final database revalidation remains mandatory.

The conservative per-paragraph relevance/coverage policy is unchanged. This
extends extractive answers, not free paraphrasing or semantic entailment. Those
require a separately reviewed evaluation and implementation. Existing API, event
states, database schema, retrieval, qrels and pending source status stay unchanged.
