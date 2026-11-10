"""Thin adapters around existing TradingAgents dataflows, with no core changes."""

from __future__ import annotations

from collections.abc import Callable
from datetime import date, datetime, timezone
from dataclasses import replace
from pathlib import Path
from typing import Any

import pandas as pd

from .models import (
    DEFAULT_MARKET_HISTORY_YEARS,
    CurrentFundamentalsInput,
    CurrentFundamentalsMetrics,
    DailyPriceBar,
    FinancialPeriod,
    FundamentalsAnalysisMode,
    FundamentalsHistory,
    FundamentalRecord,
    FundamentalsResult,
    FundamentalsStatus,
    FieldMetadata,
    PercentageUnit,
    PriceHistory,
    classify_history_coverage,
    normalize_analysis_mode,
)
from .universe import normalize_us_ticker


def _five_years_before(end: date) -> date:
    try:
        return end.replace(year=end.year - DEFAULT_MARKET_HISTORY_YEARS)
    except ValueError:  # 29 February
        return end.replace(year=end.year - DEFAULT_MARKET_HISTORY_YEARS, day=28)


class TradingAgentsMarketDataAdapter:
    """Map TradingAgents' cached ``load_ohlcv`` frame into vendor-neutral bars.

    The default loader is imported only when used and is the existing
    ``tradingagents.dataflows.stockstats_utils.load_ohlcv`` function. It keeps
    TradingAgents' configured ``data_cache_dir``, five-year yfinance cache, and
    its date filtering; this adapter adds no cache and no downloader.
    """

    source = "tradingagents:yfinance"

    def __init__(self, loader: Callable[[str, str], pd.DataFrame] | None = None) -> None:
        self._loader = loader
        self._yfinance_cache_configured = False

    def _configure_yfinance_cache(self) -> None:
        """Keep yfinance's sqlite cookie cache in the project data cache.

        The TradingAgents default home cache may be read-only in an integrated
        runtime.  This is a deterministic cache-location fix; it does not add
        a provider or alter price semantics.
        """
        if self._yfinance_cache_configured:
            return
        import yfinance.cache as yf_cache
        cache_dir = Path(__file__).resolve().parents[2] / "data_cache" / "yfinance"
        cache_dir.mkdir(parents=True, exist_ok=True)
        yf_cache.set_cache_location(str(cache_dir))
        self._yfinance_cache_configured = True

    def _load(self, ticker: str, end: date) -> pd.DataFrame:
        if self._loader is not None:
            return self._loader(ticker, end.isoformat())
        self._configure_yfinance_cache()
        from tradingagents.dataflows.stockstats_utils import load_ohlcv

        return load_ohlcv(ticker, end.isoformat())

    def get_daily_prices(
        self, ticker: str, start: date | None = None, end: date | None = None
    ) -> PriceHistory:
        """Return sorted, inclusive daily bars and never retain dates after ``end``."""
        requested_ticker = normalize_us_ticker(ticker)
        requested_end = end or date.today()
        requested_start = start or _five_years_before(requested_end)
        if requested_start > requested_end:
            raise ValueError("start must not be after end")

        frame = self._load(requested_ticker, requested_end)
        if "Date" not in frame.columns:
            frame = frame.reset_index()
        date_column = next((name for name in ("Date", "Datetime", "date", "index") if name in frame), None)
        if date_column is None:
            raise ValueError("TradingAgents OHLCV frame has no date column")
        missing = {"Open", "High", "Low", "Close"}.difference(frame.columns)
        if missing:
            raise ValueError(f"TradingAgents OHLCV frame is missing columns: {sorted(missing)}")

        working = frame.copy()
        working["_session_date"] = pd.to_datetime(working[date_column], errors="coerce").dt.date
        working = working.dropna(subset=["_session_date"])
        working = working[
            (working["_session_date"] >= requested_start) & (working["_session_date"] <= requested_end)
        ].sort_values("_session_date")
        if working["_session_date"].duplicated().any():
            raise ValueError("TradingAgents OHLCV frame contains duplicate session dates")

        def numeric(value: Any) -> float | None:
            if value is None or pd.isna(value):
                return None
            number = float(value)
            return number if pd.notna(number) and number not in (float("inf"), float("-inf")) else None

        bars = tuple(
            DailyPriceBar(
                ticker=requested_ticker,
                session_date=row["_session_date"],
                open=numeric(row["Open"]),
                high=numeric(row["High"]),
                low=numeric(row["Low"]),
                close=numeric(row["Close"]),
                volume=numeric(row.get("Volume")),
                source=self.source,
            )
            for row in working.to_dict("records")
        )
        first = bars[0].session_date if bars else None
        last = bars[-1].session_date if bars else None
        return PriceHistory(
            ticker=requested_ticker,
            requested_start=requested_start,
            requested_end=requested_end,
            first_observation=first,
            last_observation=last,
            coverage=classify_history_coverage(first, last),
            bars=bars,
            source=self.source,
        )


def _normalize_percent(value: float | None, unit: PercentageUnit | None) -> float | None:
    """Convert an explicitly-declared vendor unit to the internal percent convention."""
    if value is None:
        return None
    if isinstance(unit, str) and not isinstance(unit, PercentageUnit):
        try:
            unit = PercentageUnit(unit)
        except ValueError as exc:
            raise ValueError(f"invalid percentage unit: {unit!r}") from exc
    if unit is None or not isinstance(unit, PercentageUnit):
        raise ValueError("a percentage value requires an explicit PercentageUnit")
    number = float(value)
    return number * 100.0 if unit is PercentageUnit.DECIMAL else number


class TradingAgentsFundamentalsAdapter:
    """CURRENT_ANALYSIS mapping only; BACKTEST_PIT is a hard unavailable result.

    It uses yfinance's native ``Ticker.info`` dictionary plus annual statement
    DataFrames, which are the structured objects behind TradingAgents' current
    text/CSV presentation functions. It never parses that presentation text.
    Missing filing/publication/availability timestamps stay ``None``; these
    records are explicitly CURRENT_ANALYSIS-only and never PIT-safe.
    """

    source = "tradingagents:yfinance"

    def __init__(
        self,
        current_loader: Callable[[str], CurrentFundamentalsInput] | None = None,
        ticker_factory: Callable[[str], Any] | None = None,
    ) -> None:
        self._current_loader = current_loader
        self._ticker_factory = ticker_factory
        self._yfinance_cache_configured = False

    def _configure_yfinance_cache(self) -> None:
        """Use yfinance's supported cache API with a writable project cache.

        The TradingAgents cache remains the OHLCV cache used by the market
        adapter.  This separate vendor cookie/tz cache is runtime state only
        and is ignored by Git via ``**/data_cache/``.  We configure it here,
        before the first ``Ticker`` access, because yfinance's default user
        cache can be unavailable in an integrated/subprocess environment.
        """
        if self._yfinance_cache_configured:
            return
        import yfinance.cache as yf_cache

        cache_dir = Path(__file__).resolve().parents[2] / "data_cache" / "yfinance"
        cache_dir.mkdir(parents=True, exist_ok=True)
        if not cache_dir.is_dir():
            raise OSError(f"yfinance cache path is not a directory: {cache_dir}")
        yf_cache.set_cache_location(str(cache_dir))
        self._yfinance_cache_configured = True

    @staticmethod
    def _number(value: Any) -> float | None:
        if value is None or pd.isna(value):
            return None
        try:
            number = float(value)
            return number if pd.notna(number) and number not in (float("inf"), float("-inf")) else None
        except (TypeError, ValueError):
            return None

    @classmethod
    def _frame_value(cls, frame: pd.DataFrame, labels: tuple[str, ...], period: date) -> float | None:
        if frame is None or frame.empty:
            return None
        for column in frame.columns:
            parsed = pd.to_datetime(column, errors="coerce")
            if pd.isna(parsed) or parsed.date() != period:
                continue
            for label in labels:
                if label in frame.index:
                    return cls._number(frame.loc[label, column])
        return None

    @classmethod
    def _frame_value_with_label(cls, frame: pd.DataFrame, labels: tuple[str, ...], period: date) -> tuple[float | None, str | None]:
        """Return a structured statement value and the exact matched row label."""
        if frame is None or frame.empty:
            return None, None
        for label in labels:
            value = cls._frame_value(frame, (label,), period)
            if value is not None:
                return value, label
        return None, None

    @staticmethod
    def _periods(*frames: pd.DataFrame) -> tuple[date, ...]:
        periods: set[date] = set()
        for frame in frames:
            if frame is None or frame.empty:
                continue
            for column in frame.columns:
                parsed = pd.to_datetime(column, errors="coerce")
                if not pd.isna(parsed):
                    periods.add(parsed.date())
        return tuple(sorted(periods, reverse=True))

    @staticmethod
    def _metrics_from_input(raw: CurrentFundamentalsInput) -> CurrentFundamentalsMetrics:
        pb_basis = raw.pb_basis or "current_snapshot"
        field_metadata = {
            "pe": FieldMetadata(raw.source, raw.pe_basis, None, None, raw.retrieved_at),
            "pb": FieldMetadata(raw.source, pb_basis, None, None, raw.retrieved_at),
            "roe_percent": FieldMetadata(raw.source, raw.roe_basis, None, None, raw.retrieved_at),
            "dividend_payout_percent": FieldMetadata(raw.source, "current_snapshot", None, None, raw.retrieved_at),
        }
        return CurrentFundamentalsMetrics(
            revenue=raw.revenue,
            net_income=raw.net_income,
            eps=raw.eps,
            free_cash_flow=raw.free_cash_flow,
            equity=raw.equity,
            total_debt=raw.total_debt,
            cash=raw.cash,
            pe=raw.pe,
            pb=raw.pb,
            roe_percent=_normalize_percent(raw.roe_value, raw.roe_unit),
            dividend_payout_percent=_normalize_percent(
                raw.dividend_payout_value, raw.dividend_payout_unit
            ),
            pe_basis=raw.pe_basis,
            pb_basis=pb_basis,
            roe_basis=raw.roe_basis,
            field_metadata=field_metadata,
            warnings=("SHARE_CLASS_BASIS_UNCERTAIN",) if pb_basis == "share_class_unverified" else (),
        )

    def _native_ticker(self, ticker: str) -> Any:
        if self._ticker_factory is not None:
            return self._ticker_factory(ticker)
        self._configure_yfinance_cache()
        import yfinance as yf

        return yf.Ticker(ticker)

    def _from_yfinance(self, ticker: str, as_of: datetime) -> FundamentalsResult:
        retrieved_at = datetime.now(timezone.utc)
        if as_of.tzinfo is None:
            raise ValueError("as_of must be timezone-aware")
        if as_of.astimezone(timezone.utc).date() != retrieved_at.date():
            return FundamentalsResult(
                ticker=ticker, analysis_mode=FundamentalsAnalysisMode.CURRENT_ANALYSIS,
                status=FundamentalsStatus.CURRENT_AS_OF_UNSUPPORTED, source=self.source,
                retrieved_at=retrieved_at, as_of=retrieved_at, requested_as_of=as_of,
                data_effective_at=retrieved_at,
                reason="CURRENT_PROVIDER_CANNOT_SATISFY_REQUESTED_AS_OF",
            )
        try:
            ticker_obj = self._native_ticker(ticker)
            info = ticker_obj.info or {}
            income = ticker_obj.financials
            cashflow = ticker_obj.cashflow
            balance = ticker_obj.balance_sheet
        except Exception as exc:
            return FundamentalsResult(
                ticker=ticker, analysis_mode=FundamentalsAnalysisMode.CURRENT_ANALYSIS,
                status=FundamentalsStatus.CURRENT_DATA_UNAVAILABLE, source=self.source,
                retrieved_at=retrieved_at, as_of=retrieved_at, requested_as_of=as_of,
                data_effective_at=retrieved_at,
                reason=f"YFINANCE_FETCH_FAILED:{type(exc).__name__}",
            )

        if not isinstance(info, dict):
            return FundamentalsResult(
                ticker=ticker, analysis_mode=FundamentalsAnalysisMode.CURRENT_ANALYSIS,
                status=FundamentalsStatus.CURRENT_DATA_UNAVAILABLE, source=self.source,
                retrieved_at=retrieved_at, as_of=retrieved_at, requested_as_of=as_of,
                data_effective_at=retrieved_at, reason="YFINANCE_INFO_SCHEMA_INVALID",
            )
        income = income if isinstance(income, pd.DataFrame) else pd.DataFrame()
        cashflow = cashflow if isinstance(cashflow, pd.DataFrame) else pd.DataFrame()
        balance = balance if isinstance(balance, pd.DataFrame) else pd.DataFrame()
        periods = self._periods(income, cashflow, balance)
        record_periods = periods[:5]
        raw = CurrentFundamentalsInput(
            source="yfinance:info", retrieved_at=retrieved_at,
            revenue=self._number(info.get("totalRevenue")), net_income=self._number(info.get("netIncomeToCommon")),
            eps=self._number(info.get("trailingEps")), free_cash_flow=self._number(info.get("freeCashflow")),
            pe=self._number(info.get("trailingPE")), pb=self._number(info.get("priceToBook")),
            roe_value=self._number(info.get("returnOnEquity")), roe_unit=PercentageUnit.DECIMAL,
            dividend_payout_value=self._number(info.get("payoutRatio")), dividend_payout_unit=PercentageUnit.DECIMAL,
            pe_basis="trailing",
            pb_basis="share_class_unverified" if "." in ticker or "-" in ticker else "current_snapshot",
            roe_basis="vendor_defined",
        )
        metrics = self._metrics_from_input(raw)
        field_metadata = {
            "revenue": FieldMetadata("yfinance:financials", "annual_statement", None, None, retrieved_at),
            "net_income": FieldMetadata("yfinance:financials", "annual_statement", None, None, retrieved_at),
            "eps": FieldMetadata("yfinance:financials", "annual_statement", None, None, retrieved_at),
            "free_cash_flow": FieldMetadata("yfinance:cashflow", "annual_statement", None, None, retrieved_at),
            "equity": FieldMetadata("yfinance:balance_sheet", "annual_statement", None, None, retrieved_at),
            "total_debt": FieldMetadata("yfinance:balance_sheet", "annual_statement", None, None, retrieved_at),
            "cash": FieldMetadata("yfinance:balance_sheet", "annual_statement", None, None, retrieved_at),
            "pe": FieldMetadata("yfinance:info", "trailing", None, retrieved_at, retrieved_at),
            "pb": FieldMetadata("yfinance:info", raw.pb_basis, None, retrieved_at, retrieved_at),
            "roe_percent": FieldMetadata("yfinance:info", "vendor_defined", None, retrieved_at, retrieved_at),
            "dividend_payout_percent": FieldMetadata("yfinance:info", "current_snapshot", None, retrieved_at, retrieved_at),
        }
        if periods:
            for field in ("revenue", "net_income", "eps", "free_cash_flow", "equity", "total_debt", "cash"):
                meta = field_metadata[field]
                field_metadata[field] = replace(meta, period=periods[0])
        reliability_warnings: list[str] = [
            "filing_time_unavailable", "available_time_unavailable"
        ]
        if raw.pb_basis == "share_class_unverified":
            reliability_warnings.append("SHARE_CLASS_BASIS_UNCERTAIN")
        if income.empty and cashflow.empty and balance.empty:
            reliability_warnings.append("balance_sheet_data_missing")
        if not balance.empty and periods:
            if self._frame_value(balance, ("Stockholders Equity", "Total Stockholder Equity", "Common Stock Equity"), periods[0]) is None:
                reliability_warnings.append("equity_missing")
            if self._frame_value(balance, ("Total Debt",), periods[0]) is None:
                reliability_warnings.append("total_debt_missing")
            if self._frame_value(balance, ("Cash Cash Equivalents And Short Term Investments", "Cash And Cash Equivalents", "Cash Financial"), periods[0]) is None:
                reliability_warnings.append("cash_missing")
        metrics = CurrentFundamentalsMetrics(**{**metrics.__dict__, "field_metadata": field_metadata, "warnings": tuple(reliability_warnings)})
        records: list[FundamentalRecord] = []
        for index, period in enumerate(record_periods):
            revenue = self._frame_value(income, ("Total Revenue",), period)
            net_income = self._frame_value(income, ("Net Income", "Net Income Common Stockholders"), period)
            eps = self._frame_value(income, ("Diluted EPS", "Basic EPS"), period)
            gross_profit = self._frame_value(income, ("Gross Profit",), period)
            operating_income = self._frame_value(income, ("Operating Income",), period)
            diluted_average_shares = self._frame_value(income, ("Diluted Average Shares",), period)
            free_cash_flow = self._frame_value(cashflow, ("Free Cash Flow",), period)
            operating_cash_flow = self._frame_value(cashflow, ("Operating Cash Flow",), period)
            common_equity_issuance, issuance_label = self._frame_value_with_label(
                cashflow, ("Common Stock Issuance",), period
            )
            issuance_is_proxy = False
            if issuance_label is None:
                common_equity_issuance, issuance_label = self._frame_value_with_label(
                    cashflow, ("Issuance Of Capital Stock",), period
                )
                issuance_is_proxy = issuance_label is not None
            equity = self._frame_value(balance, ("Stockholders Equity", "Total Stockholder Equity", "Common Stock Equity"), period)
            total_debt = self._frame_value(balance, ("Total Debt",), period)
            total_assets = self._frame_value(balance, ("Total Assets",), period)
            current_assets = self._frame_value(balance, ("Current Assets",), period)
            current_liabilities = self._frame_value(balance, ("Current Liabilities",), period)
            long_term_debt = self._frame_value(balance, ("Long Term Debt",), period)
            cash = self._frame_value(
                balance, ("Cash Cash Equivalents And Short Term Investments", "Cash And Cash Equivalents", "Cash Financial"), period
            )
            # Historical ROE is calculated only from the annual statements.
            # For year t, the denominator is average equity_t and equity_(t-1);
            # no current info snapshot is copied into historical records.
            older_period = periods[index + 1] if index + 1 < len(periods) else None
            if older_period is not None and older_period.year != period.year - 1:
                older_period = None
            older_equity = (
                self._frame_value(balance, ("Stockholders Equity", "Total Stockholder Equity", "Common Stock Equity"), older_period)
                if older_period is not None else None
            )
            historical_roe = (
                net_income / ((equity + older_equity) / 2.0) * 100.0
                if net_income is not None and equity is not None and older_equity is not None
                and equity > 0 and older_equity > 0 else None
            )
            field_sources = {
                "revenue": "yfinance:financials", "net_income": "yfinance:financials",
                "eps": "yfinance:financials", "free_cash_flow": "yfinance:cashflow",
                "equity": "yfinance:balance_sheet", "total_debt": "yfinance:balance_sheet",
                "cash": "yfinance:balance_sheet", "roe_percent": "calculated:average_equity",
                "total_assets": "yfinance:balance_sheet", "current_assets": "yfinance:balance_sheet",
                "current_liabilities": "yfinance:balance_sheet", "long_term_debt": "yfinance:balance_sheet",
                "operating_cash_flow": "yfinance:cashflow", "gross_profit": "yfinance:financials",
                "operating_income": "yfinance:financials", "diluted_average_shares": "yfinance:financials",
                "common_equity_issuance": "yfinance:cashflow" if common_equity_issuance is not None else "",
            }
            record_metadata = {
                field: FieldMetadata(meta.source, meta.basis, period if meta.basis == "annual_statement" else meta.period, meta.as_of, meta.retrieved_at)
                for field, meta in field_metadata.items()
            }
            record_metadata["roe_percent"] = FieldMetadata(
                "calculated:average_equity", "net_income/average_equity", period, None, retrieved_at
            )
            for field, source, basis in (
                ("total_assets", "yfinance:balance_sheet", "annual_statement"),
                ("current_assets", "yfinance:balance_sheet", "annual_statement"),
                ("current_liabilities", "yfinance:balance_sheet", "annual_statement"),
                ("long_term_debt", "yfinance:balance_sheet", "annual_statement"),
                ("operating_cash_flow", "yfinance:cashflow", "annual_statement"),
                ("gross_profit", "yfinance:financials", "annual_statement"),
                ("operating_income", "yfinance:financials", "annual_statement"),
                ("diluted_average_shares", "yfinance:financials", "diluted_average_shares"),
            ):
                record_metadata[field] = FieldMetadata(source, basis, period, None, retrieved_at)
            if issuance_label is not None:
                record_metadata["common_equity_issuance"] = FieldMetadata(
                    "yfinance:cashflow", issuance_label, period, None, retrieved_at
                )
            records.append(FundamentalRecord(
                ticker=ticker, period=FinancialPeriod.ANNUAL, report_period=period,
                filing_date=None, published_at=None, available_at=None, source="yfinance",
                retrieved_at=retrieved_at, analysis_mode=FundamentalsAnalysisMode.CURRENT_ANALYSIS,
                revenue=revenue, net_income=net_income, eps=eps, free_cash_flow=free_cash_flow,
                equity=equity, total_debt=total_debt, cash=cash,
                total_assets=total_assets, current_assets=current_assets,
                current_liabilities=current_liabilities, long_term_debt=long_term_debt,
                operating_cash_flow=operating_cash_flow, gross_profit=gross_profit,
                operating_income=operating_income, diluted_average_shares=diluted_average_shares,
                shares_basis="diluted_average_shares" if diluted_average_shares is not None else None,
                common_equity_issuance=common_equity_issuance,
                common_equity_issuance_basis=issuance_label,
                common_equity_issuance_is_proxy=issuance_is_proxy,
                roe_percent=historical_roe, pe=None, pb=None, dividend_payout_percent=None,
                pe_basis=None, roe_basis="calculated_average_equity" if historical_roe is not None else None,
                field_sources=field_sources,
                field_metadata=record_metadata,
                warnings=tuple(reliability_warnings),
            ))
        if records:
            latest = records[0]
            metrics = replace(
                metrics,
                revenue=latest.revenue,
                net_income=latest.net_income,
                eps=latest.eps,
                free_cash_flow=latest.free_cash_flow,
                equity=latest.equity,
                total_debt=latest.total_debt,
                cash=latest.cash,
            )
        warnings = () if records else ("annual_report_period_unavailable",)
        valid_roe_years = sum(record.roe_percent is not None for record in records)
        history = FundamentalsHistory(
            ticker=ticker, as_of=retrieved_at, annual=tuple(records), source="yfinance",
            retrieved_at=retrieved_at, analysis_mode=FundamentalsAnalysisMode.CURRENT_ANALYSIS,
            warnings=tuple(dict.fromkeys((*warnings, *reliability_warnings))),
            raw_annual_periods=periods,
            analysis_years=record_periods,
            supporting_anchor_year=periods[len(record_periods)] if len(periods) > len(record_periods) else None,
            valid_roe_years=valid_roe_years,
        )
        return FundamentalsResult(
            ticker=ticker, analysis_mode=FundamentalsAnalysisMode.CURRENT_ANALYSIS,
            status=FundamentalsStatus.AVAILABLE, source="yfinance", retrieved_at=retrieved_at,
            as_of=retrieved_at, requested_as_of=as_of, data_effective_at=retrieved_at,
            current_metrics=metrics, current_history=history,
        )

    def get_fundamentals(
        self, ticker: str, as_of: datetime, mode: FundamentalsAnalysisMode | str
    ) -> FundamentalsResult:
        mode = normalize_analysis_mode(mode)
        if mode is FundamentalsAnalysisMode.UNSPECIFIED:
            raise ValueError("fundamentals analysis mode must be CURRENT_ANALYSIS or BACKTEST_PIT")
        normalized = normalize_us_ticker(ticker)
        if mode is FundamentalsAnalysisMode.BACKTEST_PIT:
            return FundamentalsResult(
                ticker=normalized,
                analysis_mode=mode,
                status=FundamentalsStatus.PIT_DATA_UNAVAILABLE,
                source=None,
                retrieved_at=None,
                as_of=as_of,
                requested_as_of=as_of,
                reason=(
                    "No filing_date/published_at/available_at-safe fundamentals adapter exists; "
                    "CURRENT_ANALYSIS data is never a fallback for BACKTEST_PIT."
                ),
            )
        if self._current_loader is None:
            return self._from_yfinance(normalized, as_of)
        raw = self._current_loader(normalized)
        if as_of.tzinfo is None or raw.retrieved_at.tzinfo is None:
            raise ValueError("as_of and retrieved_at must be timezone-aware")
        if as_of.astimezone(timezone.utc).date() != raw.retrieved_at.astimezone(timezone.utc).date():
            return FundamentalsResult(
                ticker=normalized, analysis_mode=mode,
                status=FundamentalsStatus.CURRENT_AS_OF_UNSUPPORTED,
                source=raw.source, retrieved_at=raw.retrieved_at,
                as_of=raw.retrieved_at, requested_as_of=as_of,
                data_effective_at=raw.retrieved_at,
                reason="CURRENT_PROVIDER_CANNOT_SATISFY_REQUESTED_AS_OF",
            )
        if ("." in normalized or "-" in normalized) and raw.pb_basis != "share_class_unverified":
            raw = replace(raw, pb_basis="share_class_unverified")
        metrics = self._metrics_from_input(raw)
        return FundamentalsResult(
            ticker=normalized,
            analysis_mode=mode,
            status=FundamentalsStatus.AVAILABLE,
            source=raw.source,
            retrieved_at=raw.retrieved_at,
            as_of=raw.retrieved_at, requested_as_of=as_of,
            data_effective_at=raw.retrieved_at,
            current_metrics=metrics,
        )
