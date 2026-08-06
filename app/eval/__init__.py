"""Automated evaluation utilities for retrieval and generation quality."""

from .metrics import DEFAULT_HIT_KS, aggregate_hit_rates, first_relevant_rank

__all__ = ["DEFAULT_HIT_KS", "aggregate_hit_rates", "first_relevant_rank"]
