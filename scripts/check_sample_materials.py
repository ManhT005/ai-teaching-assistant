"""Offline parser/scope/review check. No indexing, model calls or FAQ approval."""
import json
from pathlib import Path

from app.ingestion import SourceMetadata, deduplicate_sources, eligible_demo_chunks, parse_document, parse_faq_xlsx
from app.rag import evaluate_demo_evidence

SAMPLES = Path(__file__).resolve().parents[1] / "data" / "samples"


def load_sample_chunks():
    manifest = json.loads((SAMPLES / "manifest.json").read_text(encoding="utf-8"))
    chunks = []
    for source in manifest["documents"]:
        chunks.extend(parse_document(SAMPLES / source["path"], SourceMetadata.model_validate(source["metadata"])))
    chunks.extend(parse_faq_xlsx(SAMPLES / manifest["faq_path"]))
    return chunks


def main() -> None:
    chunks = load_sample_chunks()
    unique = deduplicate_sources(chunks)
    decision = evaluate_demo_evidence(unique, authorized_class="AUTO-N8N-01", required_topics={"rag_vs_finetuning"})
    print(json.dumps({
        "mode": "offline_parse_only", "parsed_chunks": len(chunks), "logical_chunks": len(unique),
        "eligible_by_class": {code: len(eligible_demo_chunks(unique, code)) for code in ("GS-KPI-01", "AUTO-N8N-01")},
        "rag_vs_finetuning": {"action": decision.action, "reason": decision.reason},
        "indexing": "not_implemented", "ticket_persistence": "not_implemented",
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
