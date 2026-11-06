"""Compare dense/lexical/hybrid rankings on a frozen, source-checked demo dataset."""

import argparse
import hashlib
import json
import math
from datetime import datetime, timezone
from pathlib import Path
from time import perf_counter
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy import select

from app.db import get_session_factory
from app.embedding_contract import MODEL_KEY
from app.embeddings import EmbeddingService
from app.models import Document, DocumentChunk
from app.retrieval import (
    HybridRetrievalService,
    eligible_chunk_predicates,
    resolve_authorized_scope,
)


class EvaluationCase(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str = Field(min_length=1)
    group_id: str = Field(min_length=1)
    question: str = Field(min_length=1, max_length=1000)
    label: Literal["answerable", "no_evidence", "ambiguous"]
    relevant_sections: tuple[str, ...]
    rationale: str = Field(min_length=1)

    @model_validator(mode="after")
    def validate_labels(self):
        if (self.label == "answerable") != bool(self.relevant_sections):
            raise ValueError("Only answerable cases have nonempty relevant sections")
        if len(self.relevant_sections) != len(set(self.relevant_sections)):
            raise ValueError("Relevant sections must be unique")
        return self


class EvaluationDataset(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    version: str
    notice: str
    review: dict
    source_checksums: dict[str, str] = Field(min_length=1)
    cases: tuple[EvaluationCase, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_case_ids(self):
        if len({case.id for case in self.cases}) != len(self.cases):
            raise ValueError("Evaluation case IDs must be unique")
        return self


def section_metrics(ranked_sections: list[str], relevant_sections: tuple[str, ...]) -> dict:
    if not relevant_sections:
        raise ValueError("No-evidence/ambiguous cases cannot enter relevance averages")

    top = ranked_sections[:3]
    relevant = set(relevant_sections)
    found = relevant & set(top)
    first_rank = next(
        (rank for rank, section in enumerate(top, start=1) if section in relevant), None
    )

    return {
        "hit_at_3": float(bool(found)),
        "recall_at_3": len(found) / len(relevant),
        "mrr_at_3": 1 / first_rank if first_rank is not None else 0.0,
    }


def percentile(values: list[float], fraction: float) -> float:
    if not values:
        raise ValueError("Cannot summarize an empty latency sample")

    ordered = sorted(values)
    position = (len(ordered) - 1) * fraction
    low, high = math.floor(position), math.ceil(position)
    return ordered[low] + (ordered[high] - ordered[low]) * (position - low)


def validate_corpus(session, dataset: EvaluationDataset, contexts: dict) -> dict:
    documents = session.execute(
        select(
            Document.logical_key, Document.checksum, Document.version, Document.expected_chunks
        ).where(Document.active.is_(True), Document.index_status == "READY")
    ).all()
    active = {document.logical_key: document for document in documents}

    if set(active) != set(dataset.source_checksums):
        raise ValueError("Active corpus must contain exactly the frozen logical source documents")

    for doc_id, checksum in dataset.source_checksums.items():
        if doc_id not in active or active[doc_id].checksum != checksum:
            raise ValueError(f"Evaluation corpus differs from frozen source checksum: {doc_id}")

    for group, context in contexts.items():
        allowed = set(
            session.scalars(
                select(DocumentChunk.section_id)
                .join(Document, Document.id == DocumentChunk.document_id)
                .where(*eligible_chunk_predicates(context.class_code))
            )
        )

        for case in dataset.cases:
            if case.group_id == group and not set(case.relevant_sections) <= allowed:
                raise ValueError(f"Relevant sections are absent or unauthorized for case {case.id}")

    return {
        doc_id: {
            "checksum": active[doc_id].checksum,
            "version": active[doc_id].version,
            "chunks": active[doc_id].expected_chunks,
        }
        for doc_id in dataset.source_checksums
    }


def evaluate(service: HybridRetrievalService, dataset: EvaluationDataset) -> dict:
    with service.sessions() as session:
        contexts = {
            group: resolve_authorized_scope(session, channel="mock", group_id=group)
            for group in sorted({case.group_id for case in dataset.cases})
        }
        corpus = validate_corpus(session, dataset, contexts)

    # Exclude one-time weight loading from per-question encoding/SQL percentiles.
    warmup_started = perf_counter()
    service.embeddings.encode_query("Kiểm tra bộ mã hóa truy vấn")
    warmup_ms = (perf_counter() - warmup_started) * 1000
    cases = []

    for case in dataset.cases:
        context = contexts[case.group_id]
        response = service.search(case.question, context, top_k=3)
        branches = {
            "dense": list(response.diagnostics.dense),
            "lexical": list(response.diagnostics.lexical),
            "hybrid": list(response.hits),
        }
        leakage = sum(
            hit.class_scope not in {context.class_code, "COMMON"}
            for hits in branches.values()
            for hit in hits
        )
        if leakage:
            raise RuntimeError(f"Cross-class retrieval detected for evaluation case {case.id}")

        rankings = {name: [hit.section_id for hit in hits[:3]] for name, hits in branches.items()}
        metrics = (
            {
                name: section_metrics(ranked, case.relevant_sections)
                for name, ranked in rankings.items()
            }
            if case.label == "answerable"
            else None
        )
        cases.append(
            {
                "id": case.id,
                "authorized_class": context.class_code,
                "label": case.label,
                "relevant_sections": list(case.relevant_sections),
                "query_sha256": hashlib.sha256(case.question.encode("utf-8")).hexdigest(),
                "lexical_terms": list(response.diagnostics.lexical_terms),
                "rankings": rankings,
                "metrics": metrics,
                "cross_class_hits": leakage,
                "timings_ms": response.diagnostics.timings.model_dump(),
                "hybrid_citations": [hit.model_dump(exclude={"content"}) for hit in response.hits],
            }
        )

    answerable = [case for case in cases if case["metrics"] is not None]
    if not answerable:
        raise ValueError("At least one answerable case is required to measure relevance")

    summary = {
        branch: {
            metric: sum(case["metrics"][branch][metric] for case in answerable) / len(answerable)
            for metric in ("hit_at_3", "recall_at_3", "mrr_at_3")
        }
        for branch in ("dense", "lexical", "hybrid")
    }
    timings = {
        field: {
            "p50": percentile([case["timings_ms"][field] for case in cases], 0.50),
            "p95": percentile([case["timings_ms"][field] for case in cases], 0.95),
        }
        for field in cases[0]["timings_ms"]
    }
    return {
        "evaluated_at": datetime.now(timezone.utc).isoformat(),
        "mode": "DEMO ONLY; relevance ranks are not answer confidence",
        "model": MODEL_KEY,
        "embedding_backend": type(service.embeddings).__name__,
        "dataset_version": dataset.version,
        "review": dataset.review,
        "configuration": {
            "top_k": 3,
            "dense_candidates": service.settings.retrieval_dense_candidates,
            "lexical_candidates": service.settings.retrieval_lexical_candidates,
            "max_lexical_terms": service.settings.retrieval_max_lexical_terms,
            "rrf_k": service.settings.retrieval_rrf_k,
        },
        "corpus": corpus,
        "questions": len(cases),
        "answerable_questions": len(answerable),
        "unscored_questions": len(cases) - len(answerable),
        "cross_class_hits": sum(case["cross_class_hits"] for case in cases),
        "summary": summary,
        "provisional_targets": {
            "recall_at_3_at_least_90_percent": summary["hybrid"]["recall_at_3"] >= 0.9,
            "no_more_than_2_percentage_points_recall_regression": summary["hybrid"]["recall_at_3"]
            + 0.02
            >= summary["dense"]["recall_at_3"],
        },
        "warmup_ms": warmup_ms,
        "latency_method": "One warmed serial measurement per question; not a concurrent load benchmark",
        "latency_ms": timings,
        "cases": cases,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--qrels", type=Path, default=Path("data/samples/retrieval_qrels.json"))
    parser.add_argument(
        "--output", type=Path, help="Write sanitized metrics and citations without source passages"
    )
    parser.add_argument(
        "--check-target",
        action="store_true",
        help="Exit nonzero if either provisional quality target is missed",
    )
    args = parser.parse_args()

    try:
        raw = args.qrels.read_bytes()
        dataset = EvaluationDataset.model_validate_json(raw)
        service = HybridRetrievalService(get_session_factory(), EmbeddingService())
        report = evaluate(service, dataset)
        report["qrels_sha256"] = hashlib.sha256(raw).hexdigest()

        if args.output:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(
                json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
            )

    except Exception as error:
        parser.exit(
            1,
            f"{type(error).__name__}: evaluation failed; verify qrels, frozen source corpus, model cache and database\n",
        )

    print(
        json.dumps(
            {key: value for key, value in report.items() if key != "cases"},
            ensure_ascii=False,
            indent=2,
        )
    )

    if args.check_target and not all(report["provisional_targets"].values()):
        parser.exit(2, "Provisional quality target missed; inspect the recorded cases\n")


if __name__ == "__main__":
    main()
