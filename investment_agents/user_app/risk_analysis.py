"""Deterministic portfolio risk analysis for the local user console."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, time, timedelta, timezone
from enum import StrEnum
from math import isfinite
from zoneinfo import ZoneInfo

from .models import RiskLimits


class RiskLevel(StrEnum):
    PASS = "PASS"
    WARNING = "WARNING"
    BLOCKED = "BLOCKED"


@dataclass(frozen=True)
class PositionInput:
    symbol: str
    quantity: float
    price: float
    sector: str = "UNKNOWN"
    currency: str = "USD"

    def __post_init__(self) -> None:
        symbol = self.symbol.strip().upper()
        sector = self.sector.strip().upper() or "UNKNOWN"
        object.__setattr__(self, "symbol", symbol)
        object.__setattr__(self, "sector", sector)
        if not symbol or len(symbol) > 16:
            raise ValueError("position symbol is required")
        if self.currency != "USD":
            raise ValueError("V1 risk console supports USD positions only")
        for name in ("quantity", "price"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isfinite(float(value)) or value <= 0:
                raise ValueError(f"{name} must be positive and finite")

    @property
    def market_value(self) -> float:
        return self.quantity * self.price


@dataclass(frozen=True)
class PortfolioInput:
    cash: float
    positions: tuple[PositionInput, ...]
    as_of: datetime

    def __post_init__(self) -> None:
        if isinstance(self.cash, bool) or not isfinite(float(self.cash)) or self.cash < 0:
            raise ValueError("cash must be non-negative and finite")
        if self.as_of.tzinfo is None or self.as_of.utcoffset() is None:
            raise ValueError("portfolio as_of must be timezone-aware")
        symbols = [position.symbol for position in self.positions]
        if len(symbols) != len(set(symbols)):
            raise ValueError("portfolio contains duplicate symbols")


@dataclass(frozen=True)
class RiskFinding:
    code: str
    level: RiskLevel
    message: str
    actual: float | int | str | None = None
    limit: float | int | None = None


class RiskAnalyzer:
    """Analyze concentration and exposure; never generates orders."""

    def analyze(
        self,
        portfolio: PortfolioInput,
        limits: RiskLimits,
        *,
        now: datetime | None = None,
    ) -> dict[str, object]:
        now = now or datetime.now(timezone.utc)
        if now.tzinfo is None or now.utcoffset() is None:
            raise ValueError("analysis time must be timezone-aware")
        findings: list[RiskFinding] = []
        if portfolio.as_of > now or now - portfolio.as_of > timedelta(minutes=15):
            findings.append(RiskFinding(
                "STALE_OR_FUTURE_PORTFOLIO", RiskLevel.BLOCKED,
                "持仓时间戳超过 15 分钟或来自未来，禁止形成执行授权。",
            ))

        position_value = sum(position.market_value for position in portfolio.positions)
        net_asset_value = portfolio.cash + position_value
        if net_asset_value <= 0:
            findings.append(RiskFinding("NON_POSITIVE_NAV", RiskLevel.BLOCKED, "账户净值必须大于零。"))
            return self._result(portfolio, limits, findings, 0.0, {}, {}, [])

        position_weights = {
            position.symbol: position.market_value / net_asset_value
            for position in portfolio.positions
        }
        sector_exposures: dict[str, float] = {}
        for position in portfolio.positions:
            sector_exposures[position.sector] = sector_exposures.get(position.sector, 0.0) + position.market_value / net_asset_value
            weight = position_weights[position.symbol]
            if weight > limits.max_single_position + 1e-12:
                findings.append(RiskFinding(
                    "MAX_SINGLE_POSITION_EXCEEDED", RiskLevel.BLOCKED,
                    f"{position.symbol} 单一持仓占比超过限制。", weight, limits.max_single_position,
                ))
            if position.sector == "UNKNOWN":
                findings.append(RiskFinding(
                    "UNKNOWN_SECTOR", RiskLevel.WARNING,
                    f"{position.symbol} 缺少行业分类，行业集中度可能被低估。", position.symbol,
                ))

        for sector, exposure in sector_exposures.items():
            if sector != "UNKNOWN" and exposure > limits.max_sector_exposure + 1e-12:
                findings.append(RiskFinding(
                    "MAX_SECTOR_EXPOSURE_EXCEEDED", RiskLevel.BLOCKED,
                    f"{sector} 行业暴露超过限制。", exposure, limits.max_sector_exposure,
                ))

        gross_exposure = position_value / net_asset_value
        cash_weight = portfolio.cash / net_asset_value
        if gross_exposure > limits.max_gross_exposure + 1e-12:
            findings.append(RiskFinding(
                "MAX_GROSS_EXPOSURE_EXCEEDED", RiskLevel.BLOCKED,
                "总持仓暴露超过限制。", gross_exposure, limits.max_gross_exposure,
            ))
        if cash_weight + 1e-12 < limits.minimum_cash_buffer:
            findings.append(RiskFinding(
                "MINIMUM_CASH_BUFFER_BREACHED", RiskLevel.BLOCKED,
                "现金缓冲低于限制。", cash_weight, limits.minimum_cash_buffer,
            ))
        if len(portfolio.positions) > limits.max_positions:
            findings.append(RiskFinding(
                "MAX_POSITIONS_EXCEEDED", RiskLevel.WARNING,
                "持仓数量超过组合复杂度限制。", len(portfolio.positions), limits.max_positions,
            ))
        if not portfolio.positions:
            findings.append(RiskFinding("NO_POSITIONS", RiskLevel.WARNING, "当前没有证券持仓。"))

        ranked = sorted(
            (
                {"symbol": position.symbol, "sector": position.sector,
                 "market_value": position.market_value, "weight": position_weights[position.symbol]}
                for position in portfolio.positions
            ),
            key=lambda row: row["weight"], reverse=True,
        )
        return self._result(
            portfolio, limits, findings, net_asset_value, position_weights,
            sector_exposures, ranked,
        )

    @staticmethod
    def _result(portfolio, limits, findings, nav, weights, sectors, ranked):
        if any(finding.level is RiskLevel.BLOCKED for finding in findings):
            overall = RiskLevel.BLOCKED
        elif findings:
            overall = RiskLevel.WARNING
        else:
            overall = RiskLevel.PASS
        gross = sum(weights.values())
        cash_weight = portfolio.cash / nav if nav > 0 else 0.0
        hhi = sum(weight * weight for weight in weights.values())
        return {
            "overall_status": overall.value,
            "execution_authorization": "DENIED" if overall is RiskLevel.BLOCKED else "NOT_REQUESTED",
            "summary": {
                "net_asset_value": nav,
                "cash": portfolio.cash,
                "cash_weight": cash_weight,
                "gross_exposure": gross,
                "position_count": len(portfolio.positions),
                "concentration_hhi": hhi,
                "largest_position_weight": max(weights.values(), default=0.0),
            },
            "positions": ranked,
            "sector_exposures": sectors,
            "findings": [
                {**asdict(finding), "level": finding.level.value}
                for finding in findings
            ],
            "limits": asdict(limits),
            "facts": ["分析仅使用用户提交的持仓、现金、价格与行业字段。"],
            "inferences": ["集中度和暴露指标不等同于未来收益预测。"],
            "recommendations": ["任何自动执行仍需独立的确定性授权与最新行情复核。"],
        }


class TradeSide(StrEnum):
    BUY = "BUY"
    SELL = "SELL"


@dataclass(frozen=True)
class TradePointInput:
    side: TradeSide
    symbol: str
    quantity: float
    price: float
    sector: str = "UNKNOWN"
    planned_at: datetime | None = None
    review_horizon_sessions: int = 20

    def __post_init__(self) -> None:
        object.__setattr__(self, "side", TradeSide(self.side))
        object.__setattr__(self, "symbol", self.symbol.strip().upper())
        object.__setattr__(self, "sector", self.sector.strip().upper() or "UNKNOWN")
        if not self.symbol:
            raise ValueError("trade-point symbol is required")
        for name in ("quantity", "price"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isfinite(float(value)) or value <= 0:
                raise ValueError(f"trade-point {name} must be positive and finite")
        if self.planned_at is None or self.planned_at.tzinfo is None or self.planned_at.utcoffset() is None:
            raise ValueError("trade-point planned_at must be timezone-aware")
        if self.review_horizon_sessions not in {0, 5, 20, 60}:
            raise ValueError("review_horizon_sessions must be one of 0, 5, 20, or 60")


class TradePointAnalyzer:
    """Analyze a user-authored buy/sell point without creating an order."""

    def __init__(self) -> None:
        self._risk = RiskAnalyzer()

    def analyze(
        self,
        portfolio: PortfolioInput,
        intent: TradePointInput,
        limits: RiskLimits,
        *,
        now: datetime | None = None,
    ) -> dict[str, object]:
        now = now or datetime.now(timezone.utc)
        current = self._risk.analyze(portfolio, limits, now=now)
        by_symbol = {position.symbol: position for position in portfolio.positions}
        existing = by_symbol.get(intent.symbol)
        notional = intent.quantity * intent.price
        blockers: list[str] = []
        time_warnings: list[str] = []
        if intent.planned_at < now - timedelta(minutes=5):
            blockers.append("PLANNED_TIME_IS_IN_THE_PAST")
        if intent.planned_at > now + timedelta(days=365):
            blockers.append("PLANNED_TIME_TOO_FAR_AHEAD")
        eastern = intent.planned_at.astimezone(ZoneInfo("America/New_York"))
        regular_session = eastern.weekday() < 5 and time(9, 30) <= eastern.time().replace(tzinfo=None) <= time(16, 0)
        if not regular_session:
            time_warnings.append("PLANNED_TIME_OUTSIDE_US_REGULAR_SESSION")
        time_warnings.append("US_MARKET_HOLIDAY_CALENDAR_NOT_VALIDATED")
        refresh_required = intent.planned_at > portfolio.as_of + timedelta(minutes=15)
        if refresh_required:
            time_warnings.append("PORTFOLIO_REFRESH_REQUIRED_AT_PLANNED_TIME")
        projected_cash = portfolio.cash
        if intent.side is TradeSide.BUY:
            projected_cash -= notional
            if projected_cash < 0:
                blockers.append("INSUFFICIENT_CASH_AT_USER_PRICE")
        else:
            if existing is None or intent.quantity > existing.quantity + 1e-12:
                blockers.append("SELL_QUANTITY_EXCEEDS_CURRENT_LONG_POSITION")
            projected_cash += notional
        if blockers:
            return {
                "review_status": "BLOCKED",
                "user_decision": "REQUIRED",
                "order_created": False,
                "broker_write": False,
                "blockers": blockers,
                "intent": asdict(intent) | {"side": intent.side.value, "notional": notional},
                "time_review": {
                    "planned_at": intent.planned_at.isoformat(),
                    "planned_at_us_eastern": eastern.isoformat(),
                    "regular_session_candidate": regular_session,
                    "refresh_required": refresh_required,
                    "warnings": time_warnings,
                },
                "current_risk": current,
                "projected_risk": None,
            }
        projected = dict(by_symbol)
        if intent.side is TradeSide.BUY:
            projected_quantity = (existing.quantity if existing else 0.0) + intent.quantity
            projected[intent.symbol] = PositionInput(
                intent.symbol, projected_quantity, intent.price,
                existing.sector if existing and existing.sector != "UNKNOWN" else intent.sector,
            )
        else:
            remaining = existing.quantity - intent.quantity
            if remaining <= 1e-12:
                projected.pop(intent.symbol, None)
            else:
                projected[intent.symbol] = PositionInput(intent.symbol, remaining, intent.price, existing.sector)
        projected_portfolio = PortfolioInput(projected_cash, tuple(projected.values()), portfolio.as_of)
        projected_risk = self._risk.analyze(projected_portfolio, limits)
        current_summary = current["summary"]
        projected_summary = projected_risk["summary"]
        projected_status = projected_risk["overall_status"]
        review_status = "RECHECK_REQUIRED" if refresh_required and projected_status != "BLOCKED" else projected_status
        return {
            "review_status": review_status,
            "user_decision": "REQUIRED",
            "order_created": False,
            "broker_write": False,
            "blockers": [],
            "intent": asdict(intent) | {"side": intent.side.value, "notional": notional},
            "time_review": {
                "planned_at": intent.planned_at.isoformat(),
                "planned_at_us_eastern": eastern.isoformat(),
                "regular_session_candidate": regular_session,
                "refresh_required": refresh_required,
                "warnings": time_warnings,
            },
            "current_risk": current,
            "projected_risk": projected_risk,
            "risk_delta": {
                "cash_weight": projected_summary["cash_weight"] - current_summary["cash_weight"],
                "gross_exposure": projected_summary["gross_exposure"] - current_summary["gross_exposure"],
                "largest_position_weight": projected_summary["largest_position_weight"] - current_summary["largest_position_weight"],
                "concentration_hhi": projected_summary["concentration_hhi"] - current_summary["concentration_hhi"],
            },
        }
