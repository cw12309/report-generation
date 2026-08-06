"""Dependency-free metrics shared by evaluation runners."""

from collections.abc import Iterable, Sequence

DEFAULT_HIT_KS = (1, 3, 5)


def first_relevant_rank(
    retrieved_ids: Sequence[str],
    expected_ids: Iterable[str],
) -> tuple[int | None, str | None]:
    """Return the 1-based rank and ID of the first relevant retrieval."""
    expected = {str(item) for item in expected_ids}
    for rank, chunk_id in enumerate(retrieved_ids, start=1):
        normalized = str(chunk_id)
        if normalized in expected:
            return rank, normalized
    return None, None


def hit_at_k(rank: int | None, k: int) -> int:
    """Return 1 when a relevant result is present within the first *k* rows."""
    if k <= 0:
        raise ValueError("k 必须大于 0")
    return int(rank is not None and rank <= k)


def aggregate_hit_rates(
    ranks: Iterable[int | None],
    ks: Sequence[int] = DEFAULT_HIT_KS,
) -> dict[int, float]:
    """Calculate Hit@K rates in the 0.0-1.0 range."""
    ranks = list(ranks)
    if not ranks:
        return {int(k): 0.0 for k in ks}
    return {
        int(k): sum(hit_at_k(rank, int(k)) for rank in ranks) / len(ranks)
        for k in ks
    }
