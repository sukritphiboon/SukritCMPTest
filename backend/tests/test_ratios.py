import pytest

from app.services.analytics import ratios

# 2500 GB ingested, 250 GB left after deduplication, 100 GB really stored
INGESTED, POST_DEDUP, PHYSICAL = 2500.0, 250.0, 100.0


def test_total_reduction_ratio():
    assert ratios.reduction_ratio(INGESTED, PHYSICAL) == 25.0


def test_dedup_ratio_is_the_removed_fraction():
    assert ratios.dedup_ratio(INGESTED, POST_DEDUP) == pytest.approx(0.9)
    assert ratios.dedup_factor(INGESTED, POST_DEDUP) == pytest.approx(10.0)


def test_compression_ratio():
    assert ratios.compression_ratio(POST_DEDUP, PHYSICAL) == 2.5


def test_ratios_multiply_to_the_total():
    factor = ratios.dedup_factor(INGESTED, POST_DEDUP)
    assert factor * ratios.compression_ratio(POST_DEDUP, PHYSICAL) == pytest.approx(
        ratios.reduction_ratio(INGESTED, PHYSICAL)
    )


def test_empty_pool_gives_zero_not_an_error():
    assert ratios.reduction_ratio(0, 0) == 0.0
    assert ratios.dedup_ratio(0, 0) == 0.0
    assert ratios.compression_ratio(0, 0) == 0.0
    assert ratios.dedup_factor(0, 0) == 0.0
