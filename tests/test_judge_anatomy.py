"""Judge anatomy helpers: overlap, thirds, direction, median, cluster bootstrap, Spearman."""

import pytest

from tenor.evals import judge_anatomy as J


def test_content_overlap_uses_best_bullet_and_ignores_stop_words() -> None:
    item = "The remote will have rubber buttons."
    bullets = ["Battery: simple battery chosen", "Rubber button on the remote"]
    assert J.content_words(item) == {"remote", "rubber", "button"}
    assert J.content_overlap(item, bullets) == 1.0
    assert J.content_overlap(item, bullets[:1]) == 0.0
    assert J.content_overlap(item, []) == 0.0
    assert J.content_overlap("the of and", bullets) == 0.0


def test_overlap_bins_are_frozen() -> None:
    assert [J.overlap_bin(v) for v in (0.0, 0.29, 0.30, 0.39, 0.40, 1.0)] == [
        "low", "low", "mid", "mid", "high", "high",
    ]  # fmt: skip


def test_third_splits_a_list_in_three() -> None:
    assert [J.third(i, 6) for i in range(6)] == ["first"] * 2 + ["middle"] * 2 + ["last"] * 2
    assert [J.third(i, 4) for i in range(4)] == ["first", "first", "middle", "last"]
    with pytest.raises(ValueError):
        J.third(4, 4)


def test_direction_and_median() -> None:
    assert J.direction("none", "full", J.COVERAGE) == "harsh"
    assert J.direction("full", "partial", J.COVERAGE) == "lenient"
    assert J.direction("partial", "partial", J.COVERAGE) == "same"
    assert J.median_verdict(["full", "none", "partial"]) == "partial"
    assert J.median_verdict(["none", "none", "full"]) == "none"
    with pytest.raises(ValueError):
        J.median_verdict(["none", "full"])


def test_cluster_rate_is_seeded_and_keeps_clusters_together() -> None:
    rows = [("a", True), ("a", True), ("b", False), ("b", False), ("c", True), ("c", False)]
    first = J.cluster_rate(rows, seed=42)
    assert first[:2] == (3, 6)
    assert first == J.cluster_rate(rows, seed=42)
    lo, hi = first[2], first[3]
    assert lo is not None and hi is not None and 0.0 <= lo <= 0.5 <= hi <= 1.0
    assert J.cluster_rate([("a", True), ("a", False)], seed=1) == (1, 2, None, None)
    # Every cluster has the same rate, so resampling clusters cannot move it.
    assert J.cluster_rate([("a", True), ("a", False), ("b", True), ("b", False)], 7)[2:] == (
        0.5,
        0.5,
    )


def test_spearman_handles_ties_and_constants() -> None:
    assert J.spearman([1, 2, 3, 4], [10, 20, 30, 40]) == pytest.approx(1.0)
    assert J.spearman([1, 2, 3, 4], [4, 3, 2, 1]) == pytest.approx(-1.0)
    assert J.spearman([1, 1, 1], [1, 2, 3]) is None
    assert J.spearman([1, 2], [1, 2]) is None
    assert J.spearman([1, 2, 2, 3], [1, 2, 2, 3]) == pytest.approx(1.0)
