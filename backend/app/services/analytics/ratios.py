"""Data reduction formulas (all inputs in the same unit, for example bytes or GB).

* total reduction ratio  = ingested / physical          (25.0 means 25:1)
* deduplication ratio    = (ingested - post_dedup) / ingested   (a 0-1 fraction: 0.9 = 90 % removed)
* compression ratio      = post_dedup / physical        (N:1)

``physical`` is what is really stored on disk, ``post_dedup`` what is left after deduplication but
before compression, ``ingested`` the logical data written by the backup clients.
"""

from __future__ import annotations


def reduction_ratio(ingested: float, physical: float) -> float:
    return ingested / physical if physical > 0 else 0.0


def dedup_ratio(ingested: float, post_dedup: float) -> float:
    return (ingested - post_dedup) / ingested if ingested > 0 else 0.0


def compression_ratio(post_dedup: float, physical: float) -> float:
    return post_dedup / physical if physical > 0 else 0.0


def dedup_factor(ingested: float, post_dedup: float) -> float:
    """The same deduplication result written as N:1 (ingested : post_dedup)."""
    return ingested / post_dedup if post_dedup > 0 else 0.0
