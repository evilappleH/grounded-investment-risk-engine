"""Strict, source-backed implementation of Piotroski's nine signals.

Methodology reference: Joseph D. Piotroski (2000), "Value Investing: The Use
of Historical Financial Statement Information to Separate Winners from
Losers", Journal of Accounting Research 38 (Supplement), pp. 1-41,
DOI: 10.2307/2672906.

This implementation maps only structured annual statement fields. It does not
use FCF as CFO, total debt as long-term debt, or diluted average shares as an
EQOFFER substitute. The original study used Compustat point-in-time annual
data; yfinance annual statements are a modern vendor adaptation and lack
reliable filing/availability timestamps, so these results are
CURRENT_ANALYSIS diagnostics and not PIT backtest data.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from math import isfinite
from typing import Mapping, Sequence

from investment_agents.data.models import FinancialPeriod, FundamentalRecord


METHODOLOGY_REFERENCE = {
    "author": "Joseph D. Piotroski",
    "title": "Value Investing: The Use of Historical Financial Statement Information to Separate Winners from Losers",
    "year": 2000,
    "doi": "10.2307/2672906",
    "source_url": "https://doi.org/10.2307/2672906",
}
PIOTROSKI_METHODOLOGY_VERSION = "piotroski-2000-average-assets-v2"
LEGACY_TURNOVER_VARIANT_VERSION = "project-beginning-assets-turnover-legacy-v1"


@dataclass(frozen=True)
class PiotroskiComponent:
    name: str
    value: float | None
    point: int | None
    formula: str
    required_inputs: tuple[str, ...]
    basis: str
    reason: str | None = None
    is_proxy: bool = False


@dataclass(frozen=True)
class PiotroskiScore:
    ticker: str
    current_period: date | None
    prior_period: date | None
    components: tuple[PiotroskiComponent, ...]
    score: int | None
    partial_score: int | None
    computed_points: int
    available_component_count: int
    status: str
    warnings: tuple[str, ...]
    source_metadata: Mapping[str, object]
    methodology_reference: Mapping[str, object]
    methodology_version: str = PIOTROSKI_METHODOLOGY_VERSION
    official_source_aligned: bool = True


def _ratio(numerator: float | None, denominator: float | None) -> float | None:
    if numerator is None or denominator is None or denominator == 0:
        return None
    value = numerator / denominator
    return value if isfinite(value) else None


def _component(name: str, value: float | None, point: int | None, formula: str,
               required: tuple[str, ...], basis: str, reason: str | None = None,
               is_proxy: bool = False) -> PiotroskiComponent:
    return PiotroskiComponent(name, value, point, formula, required, basis, reason, is_proxy)


def _calculate_piotroski_score(
    records: Sequence[FundamentalRecord], *, legacy_beginning_assets: bool
) -> PiotroskiScore:
    """Calculate all available original nine signals from annual records.

    Records are sorted newest first. Three periods are required for the change
    signals because both current and prior ROA/leverage use beginning assets.
    A partial result never masquerades as an official 0-9 score.
    """
    if any(record.period is not FinancialPeriod.ANNUAL for record in records):
        raise ValueError("Piotroski requires annual FundamentalRecord inputs")
    if len({record.ticker for record in records}) > 1:
        raise ValueError("Piotroski inputs cannot mix tickers")
    if len({record.analysis_mode for record in records}) > 1:
        raise ValueError("Piotroski inputs cannot mix analysis modes")
    if len({record.report_period for record in records}) != len(records):
        raise ValueError("Piotroski inputs cannot contain duplicate report periods")
    ordered = sorted(records, key=lambda record: record.report_period, reverse=True)
    current = ordered[0] if ordered else None
    prior = ordered[1] if len(ordered) > 1 else None
    two_back = ordered[2] if len(ordered) > 2 else None
    components: list[PiotroskiComponent] = []

    current_roa = _ratio(current.net_income if current else None, prior.total_assets if prior else None)
    prior_roa = _ratio(prior.net_income if prior else None, two_back.total_assets if two_back else None)
    cfo_ratio = _ratio(current.operating_cash_flow if current else None, prior.total_assets if prior else None)
    current_avg_assets = _ratio(
        (current.total_assets + prior.total_assets) if current and prior and current.total_assets is not None and prior.total_assets is not None else None,
        2,
    )
    prior_avg_assets = _ratio(
        (prior.total_assets + two_back.total_assets) if prior and two_back and prior.total_assets is not None and two_back.total_assets is not None else None,
        2,
    )
    current_leverage = _ratio(current.long_term_debt if current else None, current_avg_assets)
    prior_leverage = _ratio(prior.long_term_debt if prior else None, prior_avg_assets)
    current_liquid = _ratio(current.current_assets if current else None, current.current_liabilities if current else None)
    prior_liquid = _ratio(prior.current_assets if prior else None, prior.current_liabilities if prior else None)
    current_margin = _ratio(current.gross_profit if current else None, current.revenue if current else None)
    prior_margin = _ratio(prior.gross_profit if prior else None, prior.revenue if prior else None)
    if legacy_beginning_assets:
        current_turn = _ratio(current.revenue if current else None, prior.total_assets if prior else None)
        prior_turn = _ratio(prior.revenue if prior else None, two_back.total_assets if two_back else None)
        turnover_formula = "legacy: revenue_t / total_assets_(t-1) improves"
        turnover_basis = "LEGACY_PROJECT_VARIANT: beginning-of-year total assets"
        turnover_required = ("revenue", "prior_total_assets", "two_back_total_assets")
    else:
        current_turn = _ratio(current.revenue if current else None, current_avg_assets)
        prior_turn = _ratio(prior.revenue if prior else None, prior_avg_assets)
        turnover_formula = "revenue_t / average_total_assets_t improves"
        turnover_basis = "Piotroski source definition: net sales / average total assets"
        turnover_required = ("revenue", "current_assets", "prior_assets", "two_back_assets")

    components.append(_component("F_ROA", current_roa, None if current_roa is None else int(current_roa > 0),
        "net_income_t / total_assets_(t-1)", ("net_income", "prior_total_assets"), "annual_statement", "beginning assets unavailable" if current_roa is None else None))
    components.append(_component("F_CFO", cfo_ratio, None if cfo_ratio is None else int(cfo_ratio > 0),
        "operating_cash_flow_t / total_assets_(t-1)", ("operating_cash_flow", "prior_total_assets"), "annual_statement", "beginning assets or CFO unavailable" if cfo_ratio is None else None))
    delta_roa = current_roa - prior_roa if current_roa is not None and prior_roa is not None else None
    components.append(_component("F_DELTA_ROA", delta_roa, None if delta_roa is None else int(delta_roa > 0),
        "ROA_t - ROA_(t-1)", ("current_roa", "prior_roa", "two_back_total_assets"), "annual_statement", "prior ROA unavailable" if delta_roa is None else None))
    accrual = cfo_ratio - current_roa if cfo_ratio is not None and current_roa is not None else None
    components.append(_component("F_ACCRUAL", accrual, None if accrual is None else int(cfo_ratio > current_roa),
        "CFO_t/assets_(t-1) > ROA_t", ("operating_cash_flow", "net_income", "prior_total_assets"), "annual_statement", "CFO or ROA unavailable" if accrual is None else None))
    delta_leverage = prior_leverage - current_leverage if current_leverage is not None and prior_leverage is not None else None
    components.append(_component("F_DELTA_LEVER", delta_leverage, None if delta_leverage is None else int(delta_leverage > 0),
        "long_term_debt/average_assets decreases", ("current_long_term_debt", "prior_long_term_debt", "asset_periods"), "long_term_debt / average_total_assets", "long-term debt or beginning assets unavailable" if delta_leverage is None else None))
    delta_liquid = current_liquid - prior_liquid if current_liquid is not None and prior_liquid is not None else None
    components.append(_component("F_DELTA_LIQUID", delta_liquid, None if delta_liquid is None else int(delta_liquid > 0),
        "current_ratio_t > current_ratio_(t-1)", ("current_assets", "current_liabilities", "prior_current_ratio"), "annual_statement", "current ratio unavailable" if delta_liquid is None else None))

    issuance = current.common_equity_issuance if current else None
    confirmed_issuance_bases = {
        "Common Stock Issuance",
        "confirmed_common_equity_issuance",
        "SEC_COMMON_EQUITY_ISSUANCE",
    }
    issuance_confirmed = bool(
        current
        and not current.common_equity_issuance_is_proxy
        and current.common_equity_issuance_basis in confirmed_issuance_bases
    )
    issuance_point = None if issuance is None or not issuance_confirmed else int(issuance <= 0)
    issuance_reason = "common equity issuance field unavailable" if issuance is None else None
    if current and (current.common_equity_issuance_is_proxy or not issuance_confirmed):
        issuance_point = None
        issuance_reason = "common-equity issuance not confirmed; strict EQOFFER unavailable"
    components.append(_component("F_EQOFFER", issuance, issuance_point,
        "no common equity issuance in the year", ("common_equity_issuance",), current.common_equity_issuance_basis if current and current.common_equity_issuance_basis else "unavailable", issuance_reason, bool(current and current.common_equity_issuance_is_proxy)))
    delta_margin = current_margin - prior_margin if current_margin is not None and prior_margin is not None else None
    components.append(_component("F_DELTA_MARGIN", delta_margin, None if delta_margin is None else int(delta_margin > 0),
        "gross_profit/revenue improves", ("gross_profit", "revenue", "prior_margin"), "annual_statement", "gross margin unavailable" if delta_margin is None else None))
    delta_turn = current_turn - prior_turn if current_turn is not None and prior_turn is not None else None
    components.append(_component("F_DELTA_TURN", delta_turn, None if delta_turn is None else int(delta_turn > 0),
        turnover_formula, turnover_required, turnover_basis, "turnover inputs unavailable" if delta_turn is None else None))

    points = [component.point for component in components]
    available = sum(point is not None for point in points)
    computed_points = sum(point or 0 for point in points)
    complete = available == 9
    warnings = tuple(component.name for component in components if component.point is None)
    if legacy_beginning_assets:
        warnings = (*warnings, "LEGACY_PROJECT_TURNOVER_VARIANT_NOT_OFFICIAL")
    return PiotroskiScore(
        ticker=current.ticker if current else "",
        current_period=current.report_period if current else None,
        prior_period=prior.report_period if prior else None,
        components=tuple(components),
        score=computed_points if complete and not legacy_beginning_assets else None,
        partial_score=computed_points if (not complete or legacy_beginning_assets) and available else None,
        computed_points=computed_points,
        available_component_count=available,
        status=("LEGACY_VARIANT_COMPLETE" if complete else "LEGACY_VARIANT_PARTIAL") if legacy_beginning_assets else ("COMPLETE" if complete else "PARTIAL"),
        warnings=warnings,
        source_metadata={"source": current.source if current else None, "analysis_mode": current.analysis_mode.value if current else None},
        methodology_reference=METHODOLOGY_REFERENCE,
        methodology_version=LEGACY_TURNOVER_VARIANT_VERSION if legacy_beginning_assets else PIOTROSKI_METHODOLOGY_VERSION,
        official_source_aligned=not legacy_beginning_assets,
    )


def calculate_piotroski_score(records: Sequence[FundamentalRecord]) -> PiotroskiScore:
    """Calculate the formal source-aligned Piotroski score.

    F_DELTA_TURN uses net sales divided by average total assets. Complete output
    is versioned as ``piotroski-2000-average-assets-v2``.
    """
    return _calculate_piotroski_score(records, legacy_beginning_assets=False)


def calculate_piotroski_score_legacy_beginning_assets(
    records: Sequence[FundamentalRecord],
) -> PiotroskiScore:
    """Return the former project turnover variant for comparison only.

    The result is deliberately not ``COMPLETE`` and never exposes an official
    score, so ranking cannot consume it as formal Piotroski evidence.
    """
    return _calculate_piotroski_score(records, legacy_beginning_assets=True)
