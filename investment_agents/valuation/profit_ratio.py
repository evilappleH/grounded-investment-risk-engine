"""Custom Profit-Ratio (PR) valuation factor.

PR is a project-specific analytical factor inspired generally by the idea of
considering business quality and price.  It is not attributed to Warren Buffett
or Charlie Munger, is not a recommendation, and cannot determine a trade.

``roe_percent`` is always a percentage-number, not a decimal: 15% is ``15.0``.
The standard formula is therefore ``PR = PE / roe_percent``.  Do not divide by
100 again.  Conversion from an external decimal (for example ``0.15``) belongs
to a future data adapter, never to this calculation module.

The normalized project metric is ``PR = PE_TTM / ROE_pct``. The compatibility
field ``pr_original_scale`` is exactly ``100 * PR`` and is never the main
metric or a qualification threshold.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from math import isfinite


class ROEMode(StrEnum):
    """Period supplied by an approved future fundamentals data boundary."""

    TTM = "ttm"
    THREE_YEAR_AVERAGE = "3y_average"
    FIVE_YEAR_AVERAGE = "5y_average"


class PRStatus(StrEnum):
    """Availability state, deliberately separate from any investment view."""

    AVAILABLE = "PR_AVAILABLE"
    CORRECTION_UNAVAILABLE = "PR_CORRECTION_UNAVAILABLE"
    UNAVAILABLE = "PR_UNAVAILABLE"
    UNAVAILABLE_NON_TTM_PE = "PR_UNAVAILABLE_NON_TTM_PE"


class PRCycleStatus(StrEnum):
    """Explicit state for a future multi-year ROE calculation."""

    NOT_COMPUTED = "PR_CYCLE_NOT_COMPUTED"
    UNAVAILABLE = "PR_CYCLE_UNAVAILABLE"
    AVAILABLE = "PR_CYCLE_AVAILABLE"


@dataclass(frozen=True)
class PayoutCorrection:
    """Dividend-payout correction result; absence never fabricates a multiplier."""

    factor: float | None
    status: str
    warning: str | None = None


@dataclass(frozen=True)
class PRMetrics:
    """Point-in-time inputs and output for the custom PR factor.

    ``roe_percent`` and ``dividend_payout_percent`` use percentage-number units: 15% is
    represented as ``15.0``. ``data_as_of`` is retained exactly as supplied by
    the approved data boundary (typically an ISO date or timestamp). The
    corrected value is explicitly experimental and never a trading decision.
    """

    ticker: str
    pe: float | None
    roe_percent: float | None
    roe_period: ROEMode
    pb: float | None
    pr: float | None
    pr_original_scale: float | None
    dividend_payout_percent: float | None
    correction_factor: float | None
    pr_corrected: float | None
    data_as_of: str | None
    status: PRStatus
    warnings: tuple[str, ...]
    pr_from_pb_roe: float | None = None
    pr_corrected_experimental: bool = True
    pb_basis: str | None = None
    roe_basis: str | None = None
    earnings_positive: bool | None = None
    pe_basis: str | None = None
    project_reference_band: tuple[float, float] = (0.4, 0.6)


@dataclass(frozen=True)
class PRCycleMetrics:
    """Reserved cycle-aware result; no synthetic multi-year ROE is inferred."""

    pr_cycle: float | None
    pr_cycle_pb: float | None
    status: PRCycleStatus
    warnings: tuple[str, ...]
    roe_period: ROEMode | None = None
    roe_avg_percent: float | None = None
    pe_basis: str | None = None
    pr_cycle_pe_reference: float | None = None


def _finite_number(value: float | int | None) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if isfinite(number) else None


def calculate_pr_from_pe(pe: float, roe_percent: float) -> float:
    """Return ``PE / ROE_percent``; 15% ROE must be passed as ``15.0``."""
    pe_number = _finite_number(pe)
    roe_number = _finite_number(roe_percent)
    if pe_number is None or roe_number is None:
        raise ValueError("PE and ROE must be finite numbers")
    if pe_number <= 0 or roe_number <= 0:
        raise ValueError("PE and ROE must be positive to calculate PR")
    return pe_number / roe_number


def calculate_pr_from_pb(pb: float, roe_percent: float) -> float:
    """Return the equivalent ``PB * 100 / ROE_percent**2`` expression."""
    pb_number = _finite_number(pb)
    roe_number = _finite_number(roe_percent)
    if pb_number is None or roe_number is None:
        raise ValueError("PB and ROE must be finite numbers")
    if pb_number <= 0 or roe_number <= 0:
        raise ValueError("PB and ROE must be positive to calculate PR")
    return pb_number * 100.0 / (roe_number**2)


def payout_correction_factor(dividend_payout_percent: float | None) -> PayoutCorrection:
    """Return N for ``PR_corrected = N * PR`` using percentage-number payout.

    N=2 below or at 25%; N=1 above or at 50%; in between, it declines linearly.
    Missing payout returns an explicit unavailable state rather than an assumed N.
    """
    payout = _finite_number(dividend_payout_percent)
    if payout is None:
        return PayoutCorrection(None, "PAYOUT_MISSING", "dividend_payout_percent_missing")
    if payout < 0:
        return PayoutCorrection(None, "PAYOUT_INVALID", "dividend_payout_percent_negative")
    if payout >= 50:
        return PayoutCorrection(1.0, "PAYOUT_AVAILABLE")
    if payout <= 25:
        return PayoutCorrection(2.0, "PAYOUT_AVAILABLE")
    return PayoutCorrection(2.0 - ((payout - 25.0) / 25.0), "PAYOUT_AVAILABLE")


def calculate_pr_metrics(
    *,
    ticker: str,
    pe: float | None,
    roe_percent: float | None,
    roe_mode: ROEMode | str,
    pb: float | None = None,
    dividend_payout_percent: float | None = None,
    data_as_of: str | None = None,
    earnings_positive: bool | None = None,
    pe_basis: str | None = None,
    pb_basis: str | None = None,
    roe_basis: str | None = None,
    reliability_warnings: tuple[str, ...] = (),
) -> PRMetrics:
    """Build a point-in-time PR result without fetching data or making a view.

    ``roe_mode`` only labels the supplied ROE period (TTM/3-year/5-year); this
    module does not classify companies as cyclical.  Where a data provider
    supports it, callers should supply a PE based on adjusted earnings and set
    ``pe_basis`` accordingly.
    """
    mode = ROEMode(roe_mode)
    pe_number = _finite_number(pe)
    roe_number = _finite_number(roe_percent)
    pb_number = _finite_number(pb)
    payout_number = _finite_number(dividend_payout_percent)
    warnings: list[str] = list(reliability_warnings)
    if pb_basis == "share_class_unverified":
        warnings.append("SHARE_CLASS_BASIS_UNCERTAIN")
    if roe_basis == "vendor_defined":
        warnings.append("ROE_BASIS_VENDOR_DEFINED")
    bases = [basis for basis in (pe_basis, pb_basis, roe_basis) if basis]
    if len(set(bases)) > 1:
        warnings.append("DATA_BASIS_MISMATCH")

    if not ticker:
        warnings.append("ticker_missing")
    if not data_as_of:
        warnings.append("data_as_of_missing")
    if pe_number is None:
        warnings.append("pe_missing")
    elif pe_number <= 0:
        warnings.append("pe_non_positive")
    if roe_number is None:
        warnings.append("roe_missing")
    elif roe_number <= 0:
        warnings.append("roe_non_positive")
    if earnings_positive is False:
        warnings.append("earnings_loss")

    # The formal metric is explicitly PE_TTM/ROE_pct.  Other PE bases are
    # not silently treated as equivalent; a future forward-PE experiment must
    # use a separate metric.
    non_ttm_pe = pe_basis != "trailing"
    if non_ttm_pe:
        warnings.append("pe_basis_not_trailing")

    invalid = bool(
        not ticker
        or not data_as_of
        or pe_number is None
        or pe_number <= 0
        or roe_number is None
        or roe_number <= 0
        or earnings_positive is False
        or non_ttm_pe
    )
    if invalid:
        return PRMetrics(
            ticker=ticker,
            pe=pe_number,
            roe_percent=roe_number,
            roe_period=mode,
            pb=pb_number,
            pr=None,
            pr_original_scale=None,
            dividend_payout_percent=payout_number,
            correction_factor=None,
            pr_corrected=None,
            data_as_of=data_as_of,
            status=PRStatus.UNAVAILABLE_NON_TTM_PE if non_ttm_pe and pe_number is not None and pe_number > 0 and roe_number is not None and roe_number > 0 and earnings_positive is not False else PRStatus.UNAVAILABLE,
            warnings=tuple(dict.fromkeys(warnings)),
            pb_basis=pb_basis,
            roe_basis=roe_basis,
            earnings_positive=earnings_positive,
            pe_basis=pe_basis,
        )

    pr = calculate_pr_from_pe(pe_number, roe_number)
    pr_from_pb_roe: float | None = None
    if pb_basis != "share_class_unverified" and pb_number is not None and pb_number > 0:
        pr_from_pb_roe = calculate_pr_from_pb(pb_number, roe_number)
        if abs(pr - pr_from_pb_roe) > 1e-9:
            warnings.append("DATA_BASIS_MISMATCH")
    elif pb_number is not None:
        warnings.append("pb_non_positive")

    correction = payout_correction_factor(payout_number)
    if correction.warning:
        warnings.append(correction.warning)
    status = PRStatus.AVAILABLE if correction.factor is not None else PRStatus.CORRECTION_UNAVAILABLE
    return PRMetrics(
        ticker=ticker,
        pe=pe_number,
        roe_percent=roe_number,
        roe_period=mode,
        pb=pb_number,
        pr=pr,
        pr_original_scale=pr * 100.0,
        dividend_payout_percent=payout_number,
        correction_factor=correction.factor,
        pr_corrected=pr * correction.factor if correction.factor is not None else None,
        data_as_of=data_as_of,
        status=status,
        warnings=tuple(dict.fromkeys(warnings)),
        pr_from_pb_roe=pr_from_pb_roe,
        pr_corrected_experimental=True,
        pb_basis=pb_basis,
        roe_basis=roe_basis,
        earnings_positive=earnings_positive,
        pe_basis=pe_basis,
    )


def unavailable_pr_cycle(reason: str = "multi_year_roe_unavailable") -> PRCycleMetrics:
    """Return the fail-closed cycle result until real 3–5 year ROE exists."""
    return PRCycleMetrics(
        pr_cycle=None,
        pr_cycle_pb=None,
        status=PRCycleStatus.NOT_COMPUTED,
        warnings=(reason,),
    )


def calculate_pr_cycle(
    *,
    pe: float | None,
    roe_avg_percent: float | None,
    roe_period: ROEMode | str,
    pe_basis: str | None,
    pb: float | None = None,
    pb_basis: str | None = None,
) -> PRCycleMetrics:
    """Calculate the formal PB-based cycle PR from real ROE history.

    This function never decides whether a company is cyclical and never picks
    a window automatically. ``PE / ROE_avg`` is retained only as an explicitly
    named reference and is not an equivalent cross-check.
    """
    period = ROEMode(roe_period)
    if period not in (ROEMode.THREE_YEAR_AVERAGE, ROEMode.FIVE_YEAR_AVERAGE):
        raise ValueError("PR_cycle requires an explicit 3y_average or 5y_average ROE period")
    pe_number = _finite_number(pe)
    roe_number = _finite_number(roe_avg_percent)
    pb_number = _finite_number(pb)
    warnings: list[str] = []
    if pb_basis == "share_class_unverified":
        warnings.append("SHARE_CLASS_BASIS_UNCERTAIN")
    elif pb_basis not in ("current_snapshot", "current"):
        warnings.append("pb_basis_not_current_snapshot")
    if pb_number is None or pb_number <= 0:
        warnings.append("pb_missing_or_non_positive")
    if roe_number is None or roe_number <= 0:
        warnings.append("roe_average_unavailable")
    if warnings:
        return PRCycleMetrics(
            pr_cycle=None, pr_cycle_pb=None, status=PRCycleStatus.NOT_COMPUTED,
            warnings=tuple(warnings), roe_period=period, roe_avg_percent=roe_number,
            pe_basis=pe_basis,
        )
    pr_cycle_pb = pb_number * 100.0 / (roe_number**2)
    pr_cycle_pe_reference = None
    if pe_basis == "trailing" and pe_number is not None and pe_number > 0:
        pr_cycle_pe_reference = pe_number / roe_number
    else:
        warnings.append("pr_cycle_pe_reference_unavailable")
    return PRCycleMetrics(
        pr_cycle=pr_cycle_pb, pr_cycle_pb=pr_cycle_pb, status=PRCycleStatus.AVAILABLE,
        warnings=tuple(warnings), roe_period=period, roe_avg_percent=roe_number,
        pe_basis=pe_basis, pr_cycle_pe_reference=pr_cycle_pe_reference,
    )
