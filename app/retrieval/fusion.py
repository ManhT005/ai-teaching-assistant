"""Reciprocal rank fusion uses positions, never raw scores or probabilities."""

from collections.abc import Sequence
from dataclasses import dataclass


@dataclass(frozen=True)
class FusedRank:
    row_id: int
    dense_rank: int | None
    lexical_rank: int | None
    rrf_score: float


def reciprocal_rank_fusion(
    dense_ids: Sequence[int],
    lexical_ids: Sequence[int],
    k: int = 60,
) -> list[FusedRank]:
    if type(k) is not int or not 1 <= k <= 1000:
        raise ValueError("RRF constant must be between 1 and 1000")

    if any(type(row_id) is not int or row_id <= 0 for row_id in (*dense_ids, *lexical_ids)):
        raise ValueError("Fusion requires positive database chunk row IDs")

    # Defensive per-leg deduplication: each unique candidate contributes once.
    dense_ranks = {row_id: rank for rank, row_id in enumerate(dict.fromkeys(dense_ids), start=1)}
    lexical_ranks = {
        row_id: rank for rank, row_id in enumerate(dict.fromkeys(lexical_ids), start=1)
    }
    fused = []

    for row_id in dense_ranks.keys() | lexical_ranks.keys():
        dense_rank = dense_ranks.get(row_id)
        lexical_rank = lexical_ranks.get(row_id)
        score = sum(1 / (k + rank) for rank in (dense_rank, lexical_rank) if rank is not None)
        fused.append(FusedRank(row_id, dense_rank, lexical_rank, score))

    return sorted(fused, key=lambda hit: (-hit.rrf_score, hit.row_id))
