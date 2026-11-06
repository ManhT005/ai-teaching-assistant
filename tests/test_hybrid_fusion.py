import pytest

from app.rank_fusion import reciprocal_rank_fusion


def test_exact_rrf_scores_and_one_hit_for_both_legs():
    fused = reciprocal_rank_fusion([10, 20, 30], [20, 40, 10])

    assert [hit.row_id for hit in fused] == [20, 10, 40, 30]
    assert fused[0].rrf_score == pytest.approx(1 / 62 + 1 / 61)
    assert fused[0].dense_rank == 2 and fused[0].lexical_rank == 1
    assert fused[1].rrf_score == pytest.approx(1 / 61 + 1 / 63)
    assert fused[2].dense_rank is None


def test_equal_fused_scores_break_ties_by_database_id():
    fused = reciprocal_rank_fusion([9, 2], [2, 9])
    assert [hit.row_id for hit in fused] == [2, 9]
    assert fused[0].rrf_score == fused[1].rrf_score


def test_single_leg_and_empty_fallback_are_stable():
    assert reciprocal_rank_fusion([], []) == []
    assert [hit.row_id for hit in reciprocal_rank_fusion([7, 4], [])] == [7, 4]
    assert [hit.row_id for hit in reciprocal_rank_fusion([], [4, 7])] == [4, 7]


def test_duplicate_branch_rows_cannot_inflate_fused_score():
    assert reciprocal_rank_fusion([1, 1, 2], [2, 2]) == reciprocal_rank_fusion([1, 2], [2])


@pytest.mark.parametrize("ids, k", [([0], 60), ([True], 60), ([1], 0), ([1], 1001), ([1], True)])
def test_invalid_fusion_parameters_are_rejected(ids, k):
    with pytest.raises(ValueError):
        reciprocal_rank_fusion(ids, [], k)
