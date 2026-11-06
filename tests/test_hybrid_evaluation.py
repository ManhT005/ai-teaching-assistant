import json
from pathlib import Path

import pytest

from scripts.evaluate_retrieval import EvaluationDataset, percentile, section_metrics


def test_metrics_count_relevant_sections_without_double_counting_chunk_duplicates():
    metrics = section_metrics(["GS-02", "GS-02", "GS-04"], ("GS-02", "GS-03", "GS-04"))
    assert metrics == {"hit_at_3": 1.0, "recall_at_3": 2 / 3, "mrr_at_3": 1.0}
    assert section_metrics(["wrong", "right", "other"], ("right",))["mrr_at_3"] == 0.5
    assert section_metrics(["wrong"], ("right",))["recall_at_3"] == 0.0


def test_no_evidence_is_not_reported_as_a_retrieval_accuracy_success():
    with pytest.raises(ValueError, match="averages"):
        section_metrics(["unrelated"], ())


def test_latency_percentiles_are_interpolated_and_repeatable():
    assert percentile([4, 1, 3, 2], 0.5) == 2.5
    assert percentile([4, 1, 3, 2], 0.95) == pytest.approx(3.85)
    assert percentile([3], 0.95) == 3


def test_frozen_qrels_have_source_review_provenance_and_negative_cases():
    dataset = EvaluationDataset.model_validate_json(
        Path("data/samples/retrieval_qrels.json").read_bytes()
    )
    assert 20 <= len(dataset.cases) <= 30
    assert len(dataset.cases) == 30
    assert sum(case.label == "answerable" for case in dataset.cases) == 26
    assert dataset.review["human_review_status"] == "not_performed"
    assert all(len(checksum) == 64 for checksum in dataset.source_checksums.values())
    assert {case.group_id for case in dataset.cases} == {"mock-google-sheets", "mock-ai-automation"}


def test_duplicate_ids_and_inconsistent_labels_are_rejected():
    raw = json.loads(Path("data/samples/retrieval_qrels.json").read_text(encoding="utf-8"))
    raw["cases"][1]["id"] = raw["cases"][0]["id"]
    with pytest.raises(ValueError, match="unique"):
        EvaluationDataset.model_validate(raw)

    raw["cases"] = [raw["cases"][-1]]
    raw["cases"][0]["relevant_sections"] = ["AUTO-05"]
    with pytest.raises(ValueError, match="nonempty"):
        EvaluationDataset.model_validate(raw)
