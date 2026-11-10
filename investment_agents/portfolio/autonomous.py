"""Autonomous paper-portfolio orchestration with a shadow-first boundary.

This module deliberately stops at the deterministic Risk Gate in SHADOW_MODE.
It contains no implicit broker submission path; a future paper executor must be
explicitly injected and separately enabled.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field, replace
from datetime import datetime, timedelta, timezone
from enum import StrEnum
import hashlib
import json
import os
from pathlib import Path
from typing import Mapping, Sequence

from investment_agents.portfolio.engine import (
    POLICY_CLASSIFICATION, AllocationTarget, Holding, InstrumentId, InstrumentType,
    PortfolioState, ProposedAllocation, ResearchEligibility,
)
from investment_agents.risk.gate import KillSwitch, Quote, RiskContext, RiskDecision, RiskGate
from investment_agents.path_safety import validate_run_id


AUTONOMOUS_PORTFOLIO_ID = "AUTONOMOUS_PAPER_PORTFOLIO_V1"
STRATEGY_INITIAL_NAV_USD = 100_000.0
SMOKE_TEST_POSITION = "SMOKE_TEST_POSITION"
SHADOW_READY = "AUTONOMOUS_PAPER_PORTFOLIO_V1_SHADOW_READY"
SHADOW_READY_NO_ACTION = "AUTONOMOUS_PAPER_PORTFOLIO_V1_SHADOW_READY_NO_ACTION"
LIVE_DATA_BLOCKED = "AUTONOMOUS_PAPER_PORTFOLIO_V1_LIVE_DATA_BLOCKED"
PROVIDER_BLOCKED = "AUTONOMOUS_PAPER_PORTFOLIO_V1_PROVIDER_BLOCKED"


class PortfolioRunMode(StrEnum):
    SHADOW_MODE = "SHADOW_MODE"
    PAPER_AUTO = "PAPER_AUTO"


class PortfolioLifecycleState(StrEnum):
    RESEARCH_PENDING = "RESEARCH_PENDING"
    RESEARCH_COMPLETE = "RESEARCH_COMPLETE"
    PROPOSAL_CREATED = "PROPOSAL_CREATED"
    RISK_APPROVED = "RISK_APPROVED"
    EXECUTION_PENDING = "EXECUTION_PENDING"
    SUBMITTED = "SUBMITTED"
    ACKNOWLEDGED = "ACKNOWLEDGED"
    PARTIALLY_FILLED = "PARTIALLY_FILLED"
    FILLED = "FILLED"
    CANCELLED = "CANCELLED"
    REJECTED = "REJECTED"
    RECONCILED = "RECONCILED"
    UNKNOWN_BROKER_STATE = "UNKNOWN_BROKER_STATE"


@dataclass(frozen=True)
class PortfolioPolicyConfig:
    """Configurable project policy; no parameter is presented as optimal."""

    max_single_position_exposure: float = 0.10
    max_sector_exposure: float = 0.25
    max_daily_turnover: float = 0.20
    minimum_rebalance_interval: timedelta = timedelta(days=7)
    max_orders_per_cycle: int = 5
    max_turnover_per_cycle: float = 0.20
    max_notional_per_order: float = 5_000.0
    cash_reserve_fraction: float = 0.05
    classification: str = "EXPERIMENTAL_PORTFOLIO_POLICY"

    def __post_init__(self) -> None:
        for name in ("max_single_position_exposure", "max_sector_exposure", "max_daily_turnover", "max_turnover_per_cycle"):
            value = getattr(self, name)
            if not 0 < value <= 1:
                raise ValueError(f"{name} must be in (0, 1]")
        if self.max_orders_per_cycle <= 0 or self.max_notional_per_order <= 0:
            raise ValueError("cycle limits must be positive")
        if self.minimum_rebalance_interval.total_seconds() <= 0:
            raise ValueError("minimum_rebalance interval must be positive")
        if not 0 <= self.cash_reserve_fraction < 1:
            raise ValueError("cash reserve fraction must be in [0,1)")
        if self.classification != "EXPERIMENTAL_PORTFOLIO_POLICY":
            raise ValueError("portfolio limits must be marked EXPERIMENTAL_PORTFOLIO_POLICY")


@dataclass(frozen=True)
class LedgerPosition:
    instrument: InstrumentId
    quantity: float
    average_cost: float
    sector: str
    source: str = "STRATEGY"
    thesis_id: str | None = None

    @property
    def canonical_id(self) -> str:
        return self.instrument.canonical_id


@dataclass(frozen=True)
class PortfolioLedger:
    portfolio_id: str
    initialized_at: datetime
    initial_strategy_cash: float
    strategy_cash: float
    positions: tuple[LedgerPosition, ...] = ()
    excluded_broker_positions: tuple[Mapping[str, object], ...] = ()
    realized_pnl: float = 0.0
    last_cycle_at: datetime | None = None

    def __post_init__(self) -> None:
        if self.portfolio_id != AUTONOMOUS_PORTFOLIO_ID:
            raise ValueError("unexpected autonomous portfolio identifier")
        if self.initial_strategy_cash < 0 or self.strategy_cash < 0:
            raise ValueError("strategy cash must be non-negative")
        ids = [position.canonical_id for position in self.positions]
        if len(ids) != len(set(ids)):
            raise ValueError("duplicate strategy holding")

    @classmethod
    def initialize(
        cls,
        *,
        initial_cash: float,
        initialized_at: datetime,
        broker_positions: Sequence[Mapping[str, object]] = (),
    ) -> "PortfolioLedger":
        excluded = tuple({**dict(row), "exclusion": SMOKE_TEST_POSITION} for row in broker_positions)
        return cls(AUTONOMOUS_PORTFOLIO_ID, initialized_at, initial_cash, initial_cash, (), excluded)

    def strategy_state(self, prices: Mapping[str, float], *, as_of: datetime | None = None) -> PortfolioState:
        as_of = as_of or datetime.now(timezone.utc)
        holdings = tuple(Holding(p.instrument, p.quantity, float(prices[p.canonical_id]), p.sector, as_of) for p in self.positions if p.canonical_id in prices)
        if len(holdings) != len(self.positions):
            raise ValueError("missing price for strategy holding")
        return PortfolioState(as_of, self.strategy_cash, self.strategy_cash, holdings)

    def valuation(self, prices: Mapping[str, float], *, as_of: datetime | None = None) -> Mapping[str, object]:
        state = self.strategy_state(prices, as_of=as_of)
        return {
            "strategy_nav": state.total_value,
            "cash": state.cash_balance,
            "market_value": sum(h.market_value for h in state.positions),
            "realized_pnl": self.realized_pnl,
            "unrealized_pnl": sum((prices[h.canonical_id] - next(p.average_cost for p in self.positions if p.canonical_id == h.canonical_id)) * h.quantity for h in state.positions),
            "excluded_broker_positions": self.excluded_broker_positions,
        }


@dataclass(frozen=True)
class DecisionJournalEntry:
    timestamp: datetime
    ticker: str
    previous_position: float
    target_position: float
    reason: str
    research_run_id: str | None
    evidence_ids: tuple[str, ...]
    committee_conclusion: str | None
    policy_result: str
    risk_result: str
    quote: Mapping[str, object] | None
    order_intent_id: str | None
    broker_order_id: str | None
    fill_result: Mapping[str, object] | None
    portfolio_state_after_reconciliation: str


class PortfolioPolicyV1:
    """Deterministic equal-weight proposal builder; interpretations never size orders."""

    def __init__(self, config: PortfolioPolicyConfig | None = None) -> None:
        self.config = config or PortfolioPolicyConfig()

    def targets(
        self,
        candidates: Sequence[AllocationTarget],
        *,
        state: PortfolioState,
        now: datetime,
        thesis_invalidated: Sequence[str] = (),
        last_rebalance_at: datetime | None = None,
    ) -> tuple[AllocationTarget, ...]:
        if last_rebalance_at and now - last_rebalance_at < self.config.minimum_rebalance_interval:
            return tuple(target for target in candidates if target.instrument.canonical_id in state.positions_by_id)
        invalid = set(thesis_invalidated)
        eligible = [target for target in candidates if target.instrument.canonical_id not in invalid]
        existing = state.positions_by_id
        preserved: list[AllocationTarget] = []
        additions: list[AllocationTarget] = []
        for target in eligible:
            current = existing.get(target.instrument.canonical_id)
            if current is not None and state.total_value > 0:
                # Existing holdings are held at their current market weight;
                # a price move alone cannot create an averaging-down/sell signal.
                preserved.append(AllocationTarget(target.instrument, current.market_value / state.total_value, target.sector, target.research))
            else:
                additions.append(target)
        slots = max(0, self.config.max_orders_per_cycle - len(preserved))
        eligible = [*preserved, *additions[:slots]]
        if not eligible:
            return tuple()
        weight = min(self.config.max_single_position_exposure, 1.0 / len(eligible))
        return tuple(AllocationTarget(t.instrument, weight, t.sector, t.research) for t in eligible)

    def thesis_review_required(self, *, price_changed: bool, thesis_invalidated: bool, material_event: bool, evidence_failed: bool) -> bool:
        return bool(thesis_invalidated or material_event or evidence_failed)


@dataclass(frozen=True)
class CycleCheckpoint:
    run_id: str
    mode: PortfolioRunMode
    state: PortfolioLifecycleState
    created_at: datetime
    completed_at: datetime | None
    decision_id: str | None
    proposal_hash: str | None
    risk_hash: str | None
    submitted_order_ids: tuple[str, ...] = ()


class PortfolioCheckpointStore:
    """Atomic single-run checkpoint store; lock prevents duplicate runners."""

    def __init__(self, root: Path) -> None:
        self.root = root.resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.path = self.root / "portfolio_checkpoint.json"
        self.lock = self.root / "portfolio_cycle.lock"

    def acquire(self, run_id: str) -> None:
        validate_run_id(run_id)
        try:
            fd = os.open(self.lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            os.write(fd, run_id.encode("ascii"))
            os.close(fd)
        except FileExistsError as exc:
            raise RuntimeError("DUPLICATE_RUN") from exc

    def release(self) -> None:
        self.lock.unlink(missing_ok=True)

    def save(self, checkpoint: CycleCheckpoint) -> None:
        temp = self.path.with_suffix(".tmp")
        temp.write_text(json.dumps(asdict(checkpoint), default=str, sort_keys=True), encoding="utf-8")
        temp.replace(self.path)

    def load(self) -> CycleCheckpoint | None:
        if not self.path.exists():
            return None
        raw = json.loads(self.path.read_text(encoding="utf-8"))
        return CycleCheckpoint(run_id=raw["run_id"], mode=PortfolioRunMode(raw["mode"]), state=PortfolioLifecycleState(raw["state"]), created_at=datetime.fromisoformat(raw["created_at"]), completed_at=datetime.fromisoformat(raw["completed_at"]) if raw.get("completed_at") else None, decision_id=raw.get("decision_id"), proposal_hash=raw.get("proposal_hash"), risk_hash=raw.get("risk_hash"), submitted_order_ids=tuple(raw.get("submitted_order_ids", ())))


def benchmark_snapshot(spy_price: float | None, vt_price: float | None, baseline: Mapping[str, float]) -> Mapping[str, object]:
    """Return benchmark returns only when both prices are actually available."""
    result: dict[str, object] = {}
    for name, price in (("SPY", spy_price), ("VT", vt_price)):
        prior = baseline.get(name)
        result[name] = None if price is None or prior is None or prior <= 0 else (price / prior) - 1.0
    return result


class AutonomousPortfolioRunner:
    def __init__(self, *, policy: PortfolioPolicyV1 | None = None, risk_gate: RiskGate | None = None, checkpoints: PortfolioCheckpointStore | None = None) -> None:
        self.policy = policy or PortfolioPolicyV1()
        self.risk_gate = risk_gate or RiskGate()
        self.checkpoints = checkpoints

    def shadow_cycle(
        self,
        *,
        run_id: str,
        ledger: PortfolioLedger,
        candidates: Sequence[AllocationTarget],
        quotes: Mapping[str, Quote],
        verified_manifest: object,
        now: datetime,
        daily_turnover_fraction: float = 0.0,
        provider_failures: Sequence[str] = (),
    ) -> Mapping[str, object]:
        """Run through proposal and Risk Gate; never calls a broker."""
        run_id = validate_run_id(run_id)
        if self.checkpoints:
            self.checkpoints.acquire(run_id)
        try:
            if self.checkpoints:
                self.checkpoints.save(CycleCheckpoint(run_id, PortfolioRunMode.SHADOW_MODE, PortfolioLifecycleState.RESEARCH_PENDING, now, None, None, None, None))
            if provider_failures:
                return {"run_id": run_id, "mode": PortfolioRunMode.SHADOW_MODE.value, "status": "BLOCKED_PROVIDER_FAILURE", "provider_failures": tuple(provider_failures)}
            state = ledger.strategy_state({key: quote.price for key, quote in quotes.items()}, as_of=now)
            candidate_ids = {target.instrument.canonical_id for target in candidates}
            missing_existing = tuple(sorted(set(state.positions_by_id) - candidate_ids))
            if missing_existing:
                return {"run_id": run_id, "mode": PortfolioRunMode.SHADOW_MODE.value, "status": "BLOCKED_HOLDING_RESEARCH_MISSING", "missing_holdings": missing_existing, "would_submit": False, "broker_calls": 0}
            targets = self.policy.targets(candidates, state=state, now=now, last_rebalance_at=ledger.last_cycle_at)
            if self.checkpoints:
                self.checkpoints.save(CycleCheckpoint(run_id, PortfolioRunMode.SHADOW_MODE, PortfolioLifecycleState.RESEARCH_COMPLETE, now, None, None, None, None))
            proposal = self._proposal(state, targets, verified_manifest, run_id, now)
            if self.checkpoints:
                self.checkpoints.save(CycleCheckpoint(run_id, PortfolioRunMode.SHADOW_MODE, PortfolioLifecycleState.PROPOSAL_CREATED, now, None, proposal.proposal_id, proposal.proposal_hash, None))
            risk = self.risk_gate.evaluate(proposal, state, quotes, context=RiskContext(daily_turnover_fraction=daily_turnover_fraction), kill_switch=KillSwitch(False), checked_at=now)
            status = "SHADOW_RISK_APPROVED" if risk.status.value == "APPROVED" else "SHADOW_RISK_REJECTED"
            if self.checkpoints:
                self.checkpoints.save(CycleCheckpoint(run_id, PortfolioRunMode.SHADOW_MODE, PortfolioLifecycleState.RISK_APPROVED if risk.status.value == "APPROVED" else PortfolioLifecycleState.REJECTED, now, now, proposal.proposal_id, proposal.proposal_hash, risk.decision_hash))
            return {"run_id": run_id, "mode": PortfolioRunMode.SHADOW_MODE.value, "status": status, "proposal": proposal, "risk": risk, "would_submit": False, "broker_calls": 0}
        finally:
            if self.checkpoints:
                self.checkpoints.release()

    def run_current_sp500_shadow(
        self,
        *,
        root: Path,
        run_id: str,
        initial_strategy_cash: float = STRATEGY_INITIAL_NAV_USD,
        max_research_candidates: int = 10,
        max_llm_calls: int = 3,
        max_total_tokens: int = 5000,
        max_cycle_runtime_seconds: float = 600.0,
        acquisition_workers: int = 4,
        minimum_comparable_coverage: float = 0.80,
    ) -> Mapping[str, object]:
        """Build and verify a CURRENT_ANALYSIS S&P 500 run, then shadow it.

        The full current constituent set is scanned; the candidate budget only
        bounds downstream research selection and never changes universe size or
        ranking semantics.
        """
        if max_research_candidates <= 0 or max_cycle_runtime_seconds <= 0:
            raise ValueError("shadow operational budgets must be positive")
        if float(initial_strategy_cash) != STRATEGY_INITIAL_NAV_USD:
            raise ValueError("STRATEGY_INITIAL_NAV_USD is hard-rigged to 100000.00")
        if max_llm_calls <= 0 or max_total_tokens <= 0:
            raise ValueError("LLM operational budgets must be positive")
        if not 0 < minimum_comparable_coverage <= 1:
            raise ValueError("minimum comparable coverage must be in (0,1]")
        started = datetime.now(timezone.utc)
        from investment_agents.data import SECFirstFundamentalsProvider, SECProviderConfig, TradingAgentsFundamentalsAdapter
        from investment_agents.data.resumable import ResumableFundamentalsCache, ResumableFundamentalsProvider
        from investment_agents.ranking import rank_scan_result
        from investment_agents.research import build_research_queue
        from investment_agents.research.integrity import verify_run_manifest
        from investment_agents.research.persistence import export_research_run
        from investment_agents.scanner import CandidateScanner, SP500UniverseProvider
        # The expensive research budget is intentionally downstream of this
        # full scan. It must never turn unevaluated constituents into ranking
        # negatives or select by universe order. Acquisition is resumable.
        universe = SP500UniverseProvider().get_universe()
        # Provider-qualified namespace prevents historical YFinance cache
        # records from masquerading as SEC-first results.
        acquisition_root = Path(root) / "fundamentals_cache" / "CURRENT_ANALYSIS_SEC"
        cache = ResumableFundamentalsCache(acquisition_root)
        # SEC is the primary statement source.  YFinance is an explicit,
        # opt-in fallback only; it is never silently substituted when SEC
        # identity/access is unavailable.
        sec_ua = os.getenv("SEC_USER_AGENT", "")
        if sec_ua:
            fundamentals_source = SECFirstFundamentalsProvider(
                config=SECProviderConfig(user_agent=sec_ua, cache_dir=Path(root) / "sec_companyfacts")
            )
        elif os.getenv("SEC_PROVIDER_ALLOW_YFINANCE_FALLBACK") == "1":
            fundamentals_source = TradingAgentsFundamentalsAdapter()
        else:
            class _SECUnavailable:
                def get_fundamentals(self, ticker, as_of, mode):
                    from investment_agents.data.models import FundamentalsAnalysisMode, FundamentalsResult, FundamentalsStatus
                    return FundamentalsResult(ticker=ticker, analysis_mode=FundamentalsAnalysisMode.CURRENT_ANALYSIS,
                        status=FundamentalsStatus.CURRENT_DATA_UNAVAILABLE, source="SEC:companyfacts",
                        retrieved_at=None, as_of=as_of, reason="SEC_PROVIDER_NOT_CONFIGURED")
            fundamentals_source = _SECUnavailable()
        acquisition = ResumableFundamentalsProvider(fundamentals_source, cache, workers=acquisition_workers, retries=1)
        state_path = Path(root) / "research_acquisition_runs" / f"{run_id}.json"
        state_path.parent.mkdir(parents=True, exist_ok=True)
        state_path.write_text(json.dumps({"run_id": run_id, "state": "IN_PROGRESS", "universe_size": len(universe.constituents), "completed_tickers": 0, "updated_at": datetime.now(timezone.utc).isoformat()}, sort_keys=True, indent=2), encoding="utf-8")
        acquisition.prefetch(tuple(item.vendor_ticker for item in universe.constituents), started)
        acquisition_status = "COMPLETE" if len(acquisition.results) == len(universe.constituents) else "PARTIAL_PROVIDER_FAILURE"
        state_path.write_text(json.dumps({"run_id": run_id, "state": acquisition_status, "universe_size": len(universe.constituents), "completed_tickers": len(acquisition.results), "metrics": acquisition.metrics, "updated_at": datetime.now(timezone.utc).isoformat()}, default=str, sort_keys=True, indent=2), encoding="utf-8")
        class _Universe:
            def get_universe(self):
                return universe
        scanner = CandidateScanner(_Universe(), acquisition, as_of=started, retries=0)
        scan = scanner.scan(limit=None)
        # Apply the approved deterministic long-term profitability exclusion
        # before valuation/ranking/research.  The original scan remains
        # available for coverage reporting; excluded names never enter the
        # downstream queue.
        from investment_agents.financial import classify_long_term_profitability
        profitability_rows = {}
        retained_records = []
        for record in scan.records:
            history = getattr(getattr(record, "fundamentals_result", None), "current_history", None)
            classification = classify_long_term_profitability(record.ticker, history)
            profitability_rows[record.ticker] = classification
            if classification.status != "LONG_TERM_UNPROFITABLE":
                retained_records.append(record)
        filtered_scan = replace(scan, records=tuple(retained_records), successfully_analyzed=sum(1 for r in retained_records if r.scan_status.value == "ANALYZED"), qualified=sum(1 for r in retained_records if getattr(r.qualification_status, "value", None) == "QUALIFIED"), qualified_with_warnings=sum(1 for r in retained_records if getattr(r.qualification_status, "value", None) == "QUALIFIED_WITH_WARNINGS"), not_qualified=sum(1 for r in retained_records if getattr(r.qualification_status, "value", None) == "NOT_QUALIFIED"), insufficient_data=sum(1 for r in retained_records if getattr(r.qualification_status, "value", None) == "INSUFFICIENT_DATA"), qualified_tickers=tuple(r.ticker for r in retained_records if getattr(r.qualification_status, "value", None) in {"QUALIFIED", "QUALIFIED_WITH_WARNINGS"}))
        original_scan_counts = {"universe": scan.universe_size, "analyzed": scan.successfully_analyzed, "retained_after_profitability": len(retained_records)}
        profitability_report_path = Path(root) / "research_runs" / scan.run_id / "profitability_filter.json"
        profitability_report_path.parent.mkdir(parents=True, exist_ok=True)
        profitability_report_path.write_text(json.dumps({"run_id": scan.run_id, "universe_size": scan.universe_size, "analyzed": scan.successfully_analyzed, "rows": {ticker: asdict(classification) for ticker, classification in profitability_rows.items()}}, default=str, sort_keys=True, indent=2), encoding="utf-8")
        scan = filtered_scan
        if scan.analysis_mode != "CURRENT_ANALYSIS":
            raise RuntimeError("BACKTEST_PIT_NOT_ALLOWED")
        ranking = rank_scan_result(scan)
        queue = build_research_queue(scan, ranking)
        paths = export_research_run(scan, ranking, queue, root=root)
        manifest = verify_run_manifest(root=root, run_id=scan.run_id)
        comparable_coverage = scan.successfully_analyzed / scan.universe_size if scan.universe_size else 0.0
        broker_positions = ({"code": "US.AAPL", "quantity": 1.0, "exclusion": SMOKE_TEST_POSITION},)
        ledger = PortfolioLedger.initialize(initial_cash=initial_strategy_cash, initialized_at=started, broker_positions=broker_positions)
        baseline_path = Path(root) / "portfolio_baselines" / f"{AUTONOMOUS_PORTFOLIO_ID}_{scan.run_id}.json"
        baseline_path.parent.mkdir(parents=True, exist_ok=True)
        benchmark_values: dict[str, float | None] = {"SPY": None, "VT": None}
        baseline_path.write_text(json.dumps({"portfolio_id": AUTONOMOUS_PORTFOLIO_ID, "baseline_timestamp": started.isoformat(), "initial_strategy_cash": initial_strategy_cash, "initial_strategy_nav": initial_strategy_cash, "initial_strategy_positions": [], "excluded_smoke_test_assets": list(broker_positions), "benchmark_starting_values": benchmark_values}, sort_keys=True, indent=2), encoding="utf-8")
        if comparable_coverage < minimum_comparable_coverage:
            return {"status": PROVIDER_BLOCKED, "investment_status": LIVE_DATA_BLOCKED, "benchmark_status": "BENCHMARK_PENDING", "coverage_reason": "UNIVERSE_COVERAGE_INSUFFICIENT", "run_id": scan.run_id, "paths": paths, "manifest_hash": manifest.manifest_hash, "universe_size": scan.universe_size, "comparable_coverage": comparable_coverage, "minimum_comparable_coverage": minimum_comparable_coverage, "scan": scan, "original_scan_counts": original_scan_counts, "profitability_filter": profitability_rows, "queue": queue, "baseline_path": str(baseline_path), "selected_tickers": (), "llm_calls": 0, "llm_blockers": ("UNIVERSE_COVERAGE_INSUFFICIENT",), "shadow": {"status": "NO_ACTION_COVERAGE_BLOCKED", "would_submit": False, "broker_calls": 0}}
        # Ranking is complete before the operational budget is applied. Pareto
        # front/lane are the selection signal; source universe order is not.
        ranked_entries = list(select_research_entries(queue.entries, len(queue.entries) or 1))
        candidates = []
        llm_provider_status = "NOT_CHECKED"
        llm_blockers: list[str] = []
        llm_details: dict[str, object] = {}
        try:
            from investment_agents.llm.provider import OpenAIProvider
            llm_provider_status = "CONFIGURED" if OpenAIProvider().is_configured else "NOT_CONFIGURED"
        except Exception:
            llm_provider_status = "UNAVAILABLE"
        selected_entries = ranked_entries[:max_research_candidates]
        llm_calls = 0
        for entry in selected_entries:
            if entry.data_retrieved_at is None:
                continue
            if llm_provider_status != "CONFIGURED":
                llm_blockers.append(f"{entry.ticker}:LLM_PROVIDER_NOT_CONFIGURED")
                continue
            # Safety-critical committee synthesis is deterministic and does
            # not consume an additional provider call.  The optional narrative
            # path is deliberately not part of this runner's eligibility gate.
            if llm_calls + 2 > max_llm_calls:
                llm_blockers.append(f"{entry.ticker}:LLM_OPERATIONAL_BUDGET_EXHAUSTED")
                continue
            from investment_agents.llm import InvestorAgentRuntime, OpenAIProvider
            from investment_agents.committee import build_agent_decision, build_committee_decision, compile_claims
            model = os.getenv("OPENAI_MODEL") or "gpt-4o-mini"
            provider = OpenAIProvider()
            agent_runtime = InvestorAgentRuntime(provider, model=model, temperature=0.0, max_retries=0, max_output_tokens_per_call=max(1, max_total_tokens // max_llm_calls))
            agent_results = tuple(agent_runtime.analyze(entry.research_package, name, source_run_id=scan.run_id) for name in ("buffett", "munger"))
            llm_calls += 2
            systemic = {"OPENAI_AUTH_ERROR", "OPENAI_MODEL_NOT_FOUND", "OPENAI_MODEL_ACCESS_DENIED", "OPENAI_CREDIT_BALANCE_EXHAUSTED", "OPENAI_ORG_USAGE_LIMIT", "OPENAI_ORG_SPEND_LIMIT", "OPENAI_PROJECT_SPEND_LIMIT"}
            systemic_errors = [result.error_type for result in agent_results if result.error_type in systemic]
            if systemic_errors:
                llm_blockers.append(f"{entry.ticker}:{systemic_errors[0]}")
                llm_details[entry.ticker] = {"buffett": agent_results[0], "munger": agent_results[1]}
                break
            claims = compile_claims(entry.research_package, run_id=scan.run_id)
            buffett_decision = build_agent_decision(agent_results[0], claims, run_id=scan.run_id)
            munger_decision = build_agent_decision(agent_results[1], claims, run_id=scan.run_id)
            valuation_status = "COMPLETE" if any(claim.domain == "VALUATION" for claim in claims) else "INSUFFICIENT"
            committee = build_committee_decision(buffett_decision, munger_decision, claims, valuation_status=valuation_status, data_gaps=tuple(entry.pr_warnings) + tuple(entry.financial_warnings))
            llm_details[entry.ticker] = {"buffett": agent_results[0], "munger": agent_results[1], "buffett_decision": buffett_decision, "munger_decision": munger_decision, "committee": committee, "claims": claims, "committee_narrative": "COMMITTEE_NARRATIVE_NOT_REQUESTED"}
            if any(result.status.value != "COMPLETE" for result in agent_results) or committee.committee_status == "INSUFFICIENT_EVIDENCE":
                llm_blockers.append(f"{entry.ticker}:LLM_RESEARCH_INCOMPLETE")
                continue
            instrument = InstrumentId(entry.ticker, InstrumentType.EQUITY)
            candidates.append(AllocationTarget(
                instrument,
                min(self.policy.config.max_single_position_exposure, 1.0 / max(1, max_research_candidates)),
                entry.sector or "UNKNOWN",
                ResearchEligibility(instrument.canonical_id, scan.run_id, manifest.manifest_hash, queue.methodology_version, entry.data_retrieved_at, entry.data_retrieved_at, entry.qualification_status.value, entry.piotroski_status, "COMPLETE", warnings=tuple(entry.pr_warnings) + tuple(entry.financial_warnings), valuation_evidence_status=valuation_status, committee_status=committee.committee_status, committee_evidence_sufficiency=committee.evidence_sufficiency, committee_claim_ids=committee.shared_supporting_claim_ids, research_lane="BALANCED"),
            ))
        elapsed = (datetime.now(timezone.utc) - started).total_seconds()
        if elapsed > max_cycle_runtime_seconds:
            return {"status": "LIVE_DATA_BLOCKED_RUNTIME_BUDGET", "run_id": scan.run_id, "paths": paths, "manifest_hash": manifest.manifest_hash, "universe_size": scan.universe_size, "scan": scan, "queue": queue, "baseline_path": str(baseline_path)}
        quotes: dict[str, Quote] = {}
        quote_failures: list[str] = []
        if candidates:
            # Read-only quote snapshots are sufficient for shadow valuation; no
            # order-capable Moomoo method is reachable from this path.
            from investment_agents.integrations.moomoo.official import OfficialMoomooTransport
            transport = OfficialMoomooTransport()
            try:
                transport.connect("127.0.0.1", 11111)
                for candidate in candidates:
                    try:
                        snapshot = transport.quote_snapshot(f"US.{candidate.instrument.symbol}")
                        if snapshot.status != "AVAILABLE" or snapshot.last is None or snapshot.quoted_at is None:
                            quote_failures.append(candidate.instrument.canonical_id)
                        else:
                            quotes[candidate.instrument.canonical_id] = Quote(candidate.instrument.canonical_id, snapshot.last, snapshot.quoted_at)
                    except Exception:
                        quote_failures.append(candidate.instrument.canonical_id)
                for benchmark in ("SPY", "VT"):
                    try:
                        snapshot = transport.quote_snapshot(f"US.{benchmark}")
                        if snapshot.status == "AVAILABLE" and snapshot.last is not None:
                            benchmark_values[benchmark] = snapshot.last
                    except Exception:
                        pass
            finally:
                transport.disconnect()
        baseline_path.write_text(json.dumps({"portfolio_id": AUTONOMOUS_PORTFOLIO_ID, "baseline_timestamp": started.isoformat(), "initial_strategy_cash": initial_strategy_cash, "initial_strategy_nav": initial_strategy_cash, "initial_strategy_positions": [], "excluded_smoke_test_assets": list(broker_positions), "benchmark_starting_values": benchmark_values}, sort_keys=True, indent=2), encoding="utf-8")
        result = self.shadow_cycle(run_id=f"shadow-{scan.run_id}", ledger=ledger, candidates=tuple(candidates), quotes=quotes, verified_manifest=manifest, now=datetime.now(timezone.utc), provider_failures=tuple(f"QUOTE_UNAVAILABLE:{item}" for item in quote_failures))
        llm_status_rows = {ticker: {"buffett_status": getattr(detail.get("buffett"), "status", None), "munger_status": getattr(detail.get("munger"), "status", None), "committee_status": getattr(detail.get("committee"), "committee_status", None), "committee_narrative": detail.get("committee_narrative")} for ticker, detail in llm_details.items()}
        status_path = Path(root) / "research_runs" / scan.run_id / "shadow_llm_status.json"
        if not status_path.exists():
            status_path.write_text(json.dumps({"run_id": scan.run_id, "selected_tickers": [entry.ticker for entry in selected_entries], "llm_calls": llm_calls, "blockers": llm_blockers, "statuses": llm_status_rows}, default=str, sort_keys=True, indent=2), encoding="utf-8")
        investment_status = LIVE_DATA_BLOCKED if llm_blockers or quote_failures else (SHADOW_READY_NO_ACTION if not candidates else SHADOW_READY)
        benchmark_status = "BENCHMARK_READY" if all(value is not None for value in benchmark_values.values()) else "BENCHMARK_PENDING"
        return {"status": investment_status, "investment_status": investment_status, "benchmark_status": benchmark_status, "run_id": scan.run_id, "paths": paths, "manifest_hash": manifest.manifest_hash, "universe_size": scan.universe_size, "scan": scan, "original_scan_counts": original_scan_counts, "profitability_filter": profitability_rows, "profitability_report_path": str(profitability_report_path), "queue": queue, "baseline_path": str(baseline_path), "benchmark_values": benchmark_values, "llm_provider_status": llm_provider_status, "llm_calls": llm_calls, "llm_blockers": tuple(llm_blockers), "llm_details": llm_details, "selection_method": "PARETO_FRONT_THEN_LANE_THEN_TICKER_AFTER_FULL_SCAN", "selected_tickers": tuple(entry.ticker for entry in selected_entries), "shadow": result}

    @staticmethod
    def _proposal(state: PortfolioState, targets: Sequence[AllocationTarget], manifest: object, run_id: str, now: datetime) -> ProposedAllocation:
        from investment_agents.portfolio.engine import PortfolioEngine
        return PortfolioEngine().propose(state, targets, verified_manifest=manifest, proposal_id=run_id, decision_time=now)


def hash_decision_journal(entry: DecisionJournalEntry) -> str:
    return hashlib.sha256(json.dumps(asdict(entry), default=str, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()


def select_research_entries(entries: Sequence[object], maximum: int) -> tuple[object, ...]:
    """Select after ranking, then diversified balanced research lane.

    Pareto entries retain strict priority. Balanced entries are selected by a
    documented project heuristic: lower warning burden first, with one name
    per sector round-robin before a sector receives a second name. Ticker is
    only a deterministic tie-breaker, never the selection signal.
    """
    if maximum <= 0:
        raise ValueError("maximum must be positive")
    strict = sorted((e for e in entries if getattr(e, "lane", "") == "PARETO_RANKED"), key=lambda entry: (
        0 if getattr(entry, "lane", "") == "PARETO_RANKED" else 1,
        getattr(entry, "pareto_front", None) if getattr(entry, "pareto_front", None) is not None else 10**9,
        getattr(entry, "ticker", ""),
    ))
    balanced = [e for e in entries if getattr(e, "lane", "") in {"QUALIFIED_EVIDENCE_COMPLETE_UNRANKED", "QUALIFIED_UNRANKED"}]
    balanced.sort(key=lambda e: (len(getattr(e, "strong_warnings", ()) or ()) + len(getattr(e, "financial_warnings", ()) or ()) + len(getattr(e, "pr_warnings", ()) or ()), getattr(e, "ticker", "")))
    selected = strict[:maximum]
    remaining = [e for e in balanced if e not in selected]
    while len(selected) < maximum and remaining:
        sectors = {getattr(e, "sector", None) for e in selected[-len(set(getattr(x, "sector", None) for x in remaining)):]}
        pick = next((e for e in remaining if getattr(e, "sector", None) not in sectors), remaining[0])
        selected.append(pick); remaining.remove(pick)
    return tuple(selected[:maximum])
