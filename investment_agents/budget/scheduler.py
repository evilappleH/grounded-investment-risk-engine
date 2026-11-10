"""Research resource scheduling; deliberately not an investment ranking layer."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Any, Mapping

from investment_agents.llm import InvestorAgentRuntime, InvestorResearchResult, LLMProvider, OpenAIProvider, ResearchResultStatus
from investment_agents.committee import CommitteeRuntime, persist_committee_result
from investment_agents.research.queue import ResearchQueue, ResearchQueueEntry, jsonable
from investment_agents.path_safety import redact_secrets, safe_artifact_path, validate_run_id


TOKEN_BOUND_PROTOCOL_OVERHEAD = 8192


def conservative_input_token_bound(*messages: str) -> int:
    """Auditable tokenizer-free upper bound for text-only chat messages.

    A tokenizer token cannot carry less than one UTF-8 byte of supplied text.
    The byte count therefore upper-bounds content tokens; a large fixed reserve
    covers text-only chat framing. No tools, images, or attachments are sent by
    these runtimes.
    """
    return sum(len(message.encode("utf-8", errors="surrogatepass")) for message in messages) + TOKEN_BOUND_PROTOCOL_OVERHEAD


@dataclass(frozen=True)
class ResearchBudget:
    max_candidates: int | None = None
    max_agent_calls: int | None = None
    max_output_tokens_per_call: int | None = None
    max_total_completed_tokens: int | None = None
    allowed_agents: tuple[str, ...] = ("buffett", "munger")
    model: str | None = None
    max_llm_calls: int | None = None

    def __post_init__(self) -> None:
        for name in ("max_candidates", "max_agent_calls", "max_llm_calls", "max_output_tokens_per_call", "max_total_completed_tokens"):
            value = getattr(self, name)
            if value is not None and value <= 0:
                raise ValueError(f"{name} must be positive when provided")
        if not self.allowed_agents:
            raise ValueError("allowed_agents must not be empty")


@dataclass(frozen=True)
class ResearchExecutionPlan:
    run_id: str
    selected_tickers: tuple[str, ...]
    agents_per_ticker: Mapping[str, tuple[str, ...]]
    planned_call_count: int
    model: str | None
    budget: ResearchBudget
    selection_mode: str
    excluded_candidates: tuple[str, ...]
    unresolved_selection: tuple[str, ...]
    generated_at: datetime
    status: str
    committee_call_planned: bool = False


@dataclass(frozen=True)
class BudgetUsage:
    completed_calls: int = 0
    failed_calls: int = 0
    total_input_tokens: int = 0
    total_output_tokens: int = 0
    total_tokens: int = 0
    provider_cost: float | None = None
    hard_budget_consumed_tokens: int = 0
    reserved_token_upper_bound: int = 0


@dataclass(frozen=True)
class ExecutionRecord:
    ticker: str
    agent_name: str
    status: str
    result_path: str | None = None
    error_type: str | None = None


@dataclass(frozen=True)
class ExecutionReport:
    run_id: str
    plan_status: str
    status: str
    provider: str
    model: str | None
    started_at: datetime
    completed_at: datetime
    completed_executions: tuple[ExecutionRecord, ...]
    failed_executions: tuple[ExecutionRecord, ...]
    unexecuted_calls: tuple[ExecutionRecord, ...]
    usage: BudgetUsage
    budget_status: str


def _entry_map(queue: ResearchQueue) -> dict[str, ResearchQueueEntry]:
    result = {entry.ticker: entry for entry in queue.entries}
    if len(result) != len(queue.entries):
        raise ValueError("ResearchQueue contains duplicate tickers")
    return result


def build_execution_plan(
    queue: ResearchQueue,
    *,
    agents: tuple[str, ...] | list[str],
    selection_mode: str = "EXPLICIT",
    tickers: tuple[str, ...] | list[str] = (),
    pareto_front: int | None = None,
    budget: ResearchBudget,
    include_committee: bool = False,
) -> ResearchExecutionPlan:
    """Create a complete plan with zero provider calls and no hidden cutoff."""
    requested_agents = tuple(dict.fromkeys(agent.strip().lower() for agent in agents if agent.strip()))
    invalid_agents = tuple(agent for agent in requested_agents if agent not in budget.allowed_agents or agent not in {"buffett", "munger"})
    if not requested_agents or invalid_agents:
        return ResearchExecutionPlan(queue.run_id, (), {}, 0, budget.model, budget, selection_mode, invalid_agents, invalid_agents, datetime.now(timezone.utc), "INVALID_AGENT", include_committee)
    entries = _entry_map(queue)
    if selection_mode == "EXPLICIT":
        requested = tuple(dict.fromkeys(str(t).upper() for t in tickers))
        excluded = tuple(
            t for t in requested
            if t not in entries or getattr(entries[t].qualification_status, "value", entries[t].qualification_status)
            not in ("QUALIFIED", "QUALIFIED_WITH_WARNINGS")
        )
        selected = tuple(t for t in requested if t not in excluded)
    elif selection_mode == "PARETO_FRONT":
        if pareto_front is None:
            return ResearchExecutionPlan(queue.run_id, (), {}, 0, budget.model, budget, selection_mode, (), ("pareto_front_required",), datetime.now(timezone.utc), "INVALID_SELECTION", include_committee)
        selected = tuple(entry.ticker for entry in queue.entries if entry.lane == "PARETO_RANKED" and entry.pareto_front == pareto_front)
        excluded = ()
    else:
        return ResearchExecutionPlan(queue.run_id, (), {}, 0, budget.model, budget, selection_mode, (), ("invalid_selection_mode",), datetime.now(timezone.utc), "INVALID_SELECTION", include_committee)
    unresolved: list[str] = []
    status = "READY"
    if excluded:
        unresolved.extend(excluded)
        status = "SELECTION_INVALID"
    if budget.max_candidates is not None and len(selected) > budget.max_candidates:
        unresolved.extend(selected[budget.max_candidates:])
        status = "SELECTION_EXCEEDS_BUDGET"
    planned_agent_calls = len(selected) * len(requested_agents)
    planned = planned_agent_calls + (len(selected) if include_committee else 0)
    if budget.max_agent_calls is not None and planned_agent_calls > budget.max_agent_calls:
        unresolved.extend(selected)
        status = "BUDGET_EXCEEDED"
    if budget.max_llm_calls is not None and planned > budget.max_llm_calls:
        unresolved.extend(selected)
        status = "BUDGET_EXCEEDED"
    if status != "READY":
        return ResearchExecutionPlan(queue.run_id, selected, {ticker: requested_agents for ticker in selected}, planned, budget.model, budget, selection_mode, excluded, tuple(dict.fromkeys(unresolved)), datetime.now(timezone.utc), status, include_committee)
    return ResearchExecutionPlan(queue.run_id, selected, {ticker: requested_agents for ticker in selected}, planned, budget.model, budget, selection_mode, excluded, (), datetime.now(timezone.utc), status, include_committee)


def _add_usage(usage: BudgetUsage, result: InvestorResearchResult, *, failed: bool) -> BudgetUsage:
    item = result.usage
    return BudgetUsage(
        completed_calls=usage.completed_calls + (0 if failed else 1), failed_calls=usage.failed_calls + (1 if failed else 0),
        total_input_tokens=usage.total_input_tokens + (item.input_tokens or 0 if item else 0),
        total_output_tokens=usage.total_output_tokens + (item.output_tokens or 0 if item else 0),
        total_tokens=usage.total_tokens + (item.total_tokens or 0 if item else 0),
        provider_cost=(usage.provider_cost + item.cost if usage.provider_cost is not None and item and item.cost is not None else item.cost if item and item.cost is not None else usage.provider_cost),
        hard_budget_consumed_tokens=usage.hard_budget_consumed_tokens,
        reserved_token_upper_bound=usage.reserved_token_upper_bound,
    )


def _with_token_reservation(
    usage: BudgetUsage, reservation: int | None, observed_total: int | None
) -> BudgetUsage:
    if reservation is None:
        return usage
    consumed = observed_total if observed_total is not None else reservation
    if consumed > reservation:
        raise RuntimeError("PROVIDER_USAGE_EXCEEDED_CONSERVATIVE_TOKEN_BOUND")
    return BudgetUsage(
        completed_calls=usage.completed_calls,
        failed_calls=usage.failed_calls,
        total_input_tokens=usage.total_input_tokens,
        total_output_tokens=usage.total_output_tokens,
        total_tokens=usage.total_tokens,
        provider_cost=usage.provider_cost,
        hard_budget_consumed_tokens=usage.hard_budget_consumed_tokens + consumed,
        reserved_token_upper_bound=usage.reserved_token_upper_bound + reservation,
    )


def _reservation_allowed(usage: BudgetUsage, budget: ResearchBudget, reservation: int | None) -> bool:
    return (
        budget.max_total_completed_tokens is None
        or reservation is not None
        and usage.hard_budget_consumed_tokens + reservation <= budget.max_total_completed_tokens
    )


def execute_plan(plan: ResearchExecutionPlan, queue: ResearchQueue, provider: LLMProvider, *, temperature: float | None = 0.0, root: Path | None = None) -> ExecutionReport:
    """Execute only a preflight-approved plan; preserve failures and omissions."""
    started = datetime.now(timezone.utc)
    if plan.run_id != queue.run_id:
        raise ValueError("ResearchExecutionPlan run_id does not match ResearchQueue run_id")
    validate_run_id(plan.run_id)
    if plan.status != "READY":
        return ExecutionReport(plan.run_id, plan.status, plan.status, provider.provider_name, plan.model, started, datetime.now(timezone.utc), (), (), tuple(ExecutionRecord(t, a, "UNEXECUTED") for t in plan.selected_tickers for a in plan.agents_per_ticker[t]), BudgetUsage(), plan.status)
    if not plan.model:
        return ExecutionReport(plan.run_id, plan.status, "LLM_MODEL_NOT_CONFIGURED", provider.provider_name, None, started, datetime.now(timezone.utc), (), (), tuple(ExecutionRecord(t, a, "UNEXECUTED") for t in plan.selected_tickers for a in plan.agents_per_ticker[t]), BudgetUsage(), "LLM_MODEL_NOT_CONFIGURED")
    if isinstance(provider, OpenAIProvider) and not provider.is_configured:
        return ExecutionReport(plan.run_id, plan.status, "LLM_PROVIDER_NOT_CONFIGURED", provider.provider_name, plan.model, started, datetime.now(timezone.utc), (), (), tuple(ExecutionRecord(t, a, "UNEXECUTED") for t in plan.selected_tickers for a in plan.agents_per_ticker[t]), BudgetUsage(), "LLM_PROVIDER_NOT_CONFIGURED")
    entries = _entry_map(queue)
    expected_calls = sum(len(plan.agents_per_ticker.get(ticker, ())) for ticker in plan.selected_tickers)
    if plan.committee_call_planned:
        expected_calls += len(plan.selected_tickers)
    if expected_calls != plan.planned_call_count or any(ticker not in entries for ticker in plan.selected_tickers):
        raise ValueError("execution plan contents are inconsistent with the queue or planned call count")
    completed: list[ExecutionRecord] = []
    failed: list[ExecutionRecord] = []
    unexecuted: list[ExecutionRecord] = []
    usage = BudgetUsage()
    for ticker in plan.selected_tickers:
        package = entries[ticker].research_package
        ticker_results: list[InvestorResearchResult] = []
        for agent in plan.agents_per_ticker[ticker]:
            runtime = InvestorAgentRuntime(provider, model=plan.model, temperature=temperature, max_retries=0, max_output_tokens_per_call=plan.budget.max_output_tokens_per_call)
            reservation = None
            if plan.budget.max_total_completed_tokens is not None:
                if plan.budget.max_output_tokens_per_call is None:
                    unexecuted.append(ExecutionRecord(ticker, agent, "UNEXECUTED_TOKEN_RESERVATION_UNAVAILABLE"))
                    continue
                preview = runtime.preview(package, agent)
                reservation = conservative_input_token_bound(preview.system_prompt, preview.user_prompt) + plan.budget.max_output_tokens_per_call
                if not _reservation_allowed(usage, plan.budget, reservation):
                    unexecuted.append(ExecutionRecord(ticker, agent, "UNEXECUTED_BUDGET_EXHAUSTED"))
                    continue
            result = runtime.analyze(package, agent, source_run_id=plan.run_id)
            is_failure = result.status is not ResearchResultStatus.COMPLETE
            usage = _add_usage(usage, result, failed=is_failure)
            usage = _with_token_reservation(usage, reservation, result.usage.total_tokens if result.usage else None)
            record = ExecutionRecord(ticker, agent, result.status.value, error_type=result.error_type)
            if is_failure:
                failed.append(record)
            else:
                ticker_results.append(result)
                path = None
                if root is not None:
                    from investment_agents.llm.runtime import persist_investor_result
                    try:
                        path = str(persist_investor_result(result, root=root, source_run_id=plan.run_id))
                    except Exception as exc:
                        failed.append(ExecutionRecord(ticker, agent, "PERSISTENCE_ERROR", error_type=type(exc).__name__))
                        continue
                completed.append(ExecutionRecord(ticker, agent, result.status.value, result_path=path))
        if plan.committee_call_planned:
            committee_runtime = CommitteeRuntime(provider, model=plan.model, max_retries=0, max_output_tokens_per_call=plan.budget.max_output_tokens_per_call)
            committee_reservation = None
            if plan.budget.max_total_completed_tokens is not None and plan.budget.max_output_tokens_per_call is None:
                unexecuted.append(ExecutionRecord(ticker, "committee", "UNEXECUTED_TOKEN_RESERVATION_UNAVAILABLE"))
            else:
                if plan.budget.max_total_completed_tokens is not None:
                    committee_prompt = committee_runtime.preview(package, ticker_results)
                    committee_reservation = conservative_input_token_bound(
                        "You are a research synthesis committee. Return strict JSON research synthesis only.",
                        committee_prompt,
                    ) + int(plan.budget.max_output_tokens_per_call or 0)
                if not _reservation_allowed(usage, plan.budget, committee_reservation):
                    unexecuted.append(ExecutionRecord(ticker, "committee", "UNEXECUTED_BUDGET_EXHAUSTED"))
                    continue
                try:
                    committee = committee_runtime.synthesize(package, ticker_results, source_run_id=plan.run_id, execute=True)
                    committee_failed = committee.runtime_status != "COMPLETE"
                    if committee.usage:
                        usage = BudgetUsage(
                            completed_calls=usage.completed_calls + (0 if committee_failed else 1), failed_calls=usage.failed_calls + (1 if committee_failed else 0),
                            total_input_tokens=usage.total_input_tokens + (committee.usage.input_tokens or 0),
                            total_output_tokens=usage.total_output_tokens + (committee.usage.output_tokens or 0),
                            total_tokens=usage.total_tokens + (committee.usage.total_tokens or 0),
                            provider_cost=(usage.provider_cost or 0) + committee.usage.cost if committee.usage.cost is not None else usage.provider_cost,
                            hard_budget_consumed_tokens=usage.hard_budget_consumed_tokens,
                            reserved_token_upper_bound=usage.reserved_token_upper_bound,
                        )
                    else:
                        usage = BudgetUsage(usage.completed_calls + (0 if committee_failed else 1), usage.failed_calls + (1 if committee_failed else 0), usage.total_input_tokens, usage.total_output_tokens, usage.total_tokens, usage.provider_cost, usage.hard_budget_consumed_tokens, usage.reserved_token_upper_bound)
                    usage = _with_token_reservation(usage, committee_reservation, committee.usage.total_tokens if committee.usage else None)
                    if committee_failed:
                        failed.append(ExecutionRecord(ticker, "committee", committee.runtime_status, error_type=committee.error_type))
                    else:
                        path = None
                        if root is not None:
                            try:
                                path = str(persist_committee_result(committee, root=root, source_run_id=plan.run_id))
                            except Exception as exc:
                                failed.append(ExecutionRecord(ticker, "committee", "PERSISTENCE_ERROR", error_type=type(exc).__name__))
                                continue
                        completed.append(ExecutionRecord(ticker, "committee", "COMPLETE", result_path=path))
                except Exception as exc:
                    failed.append(ExecutionRecord(ticker, "committee", "PROVIDER_ERROR", error_type=type(exc).__name__))
                    usage = BudgetUsage(usage.completed_calls, usage.failed_calls + 1, usage.total_input_tokens, usage.total_output_tokens, usage.total_tokens, usage.provider_cost)
    status = "COMPLETE" if not failed and not unexecuted else "EXECUTION_PARTIAL"
    budget_status = "BUDGET_EXHAUSTED" if unexecuted else "WITHIN_BUDGET"
    return ExecutionReport(plan.run_id, plan.status, status, provider.provider_name, plan.model, started, datetime.now(timezone.utc), tuple(completed), tuple(failed), tuple(unexecuted), usage, budget_status)


def persist_execution_artifacts(plan: ResearchExecutionPlan, report: ExecutionReport | None, *, root: Path) -> dict[str, str]:
    run_id = validate_run_id(plan.run_id)
    if report is not None and report.run_id != run_id:
        raise ValueError("ExecutionReport run_id does not match plan run_id")
    directory = safe_artifact_path(root, "research_runs", run_id)
    directory.mkdir(parents=True, exist_ok=True)
    paths: dict[str, str] = {}
    artifacts = [("execution_plan.json", plan)]
    if report is not None:
        artifacts.append(("execution_report.json", report))
    for name, value in artifacts:
        path = directory / name
        if path.exists():
            if name == "execution_plan.json":
                existing = json.loads(path.read_text(encoding="utf-8"))
                candidate = jsonable(value)
                if existing == candidate:
                    paths[name.removesuffix(".json")] = str(path)
                    continue
                path = directory / f"execution_plan_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')}.json"
            else:
                raise FileExistsError(f"refusing to overwrite execution artifact: {path}")
        try:
            with path.open("x", encoding="utf-8") as handle:
                json.dump(redact_secrets(jsonable(value)), handle, ensure_ascii=True, indent=2, sort_keys=True)
        except FileExistsError as exc:
            raise FileExistsError(f"refusing to overwrite execution artifact: {path}") from exc
        paths[name.removesuffix(".json")] = str(path)
    return paths
