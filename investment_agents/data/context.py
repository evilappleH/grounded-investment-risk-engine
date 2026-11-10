"""Offline, point-in-time input contracts for investor personas.

This module intentionally fetches no data.  A future authorized data boundary
must construct these objects before an investor persona can be prepared.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class FundamentalsContext:
    """Fundamentals already rendered from data available at ``data_as_of``."""

    ticker: str
    data_as_of: str
    rendered_fundamentals: str
    source: str | None = None
