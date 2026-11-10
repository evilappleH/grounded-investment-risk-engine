"""Minimal no-LLM/no-trading Step 6A CLI."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from investment_agents.data import StaticUniverseProvider, TradingAgentsFundamentalsAdapter
from investment_agents.scanner import CandidateScanner, SP500UniverseProvider
from investment_agents.ranking import rank_scan_result
from investment_agents.research import build_research_queue, export_research_run, verify_run_manifest
from investment_agents.research.persistence import stable_cache_root
from investment_agents.llm import InvestorAgentRuntime, OpenAIProvider, persist_investor_result
from investment_agents.budget import ResearchBudget, build_execution_plan, execute_plan, persist_execution_artifacts
from investment_agents.path_safety import safe_artifact_path, validate_run_id


def main() -> None:
    parser = argparse.ArgumentParser(prog="python -m investment_agents")
    sub = parser.add_subparsers(dest="command", required=True)
    scan = sub.add_parser("scan")
    scan.add_argument("--universe", choices=("sp500", "custom"), default="sp500")
    scan.add_argument("--limit", type=int, default=None, help="development scan limit; not ranking")
    scan.add_argument("--tickers", nargs="*", default=[])
    scan.add_argument("--rank", action="store_true", help="build unweighted Pareto research fronts")
    scan.add_argument("--export-research", action="store_true", help="persist auditable Step 6C artifacts")
    research = sub.add_parser("research", help="preview or explicitly execute one ResearchPackage")
    research.add_argument("--run-id", required=True)
    research.add_argument("--ticker", default=None)
    research.add_argument("--tickers", nargs="*", default=[])
    research.add_argument("--pareto-front", type=int, default=None)
    research.add_argument("--agents", default="buffett,munger")
    research.add_argument("--dry-run", action="store_true", help="preview prompts without an LLM call")
    research.add_argument("--execute-llm", action="store_true", help="explicitly permit one-ticker LLM execution")
    research.add_argument("--model", default=None)
    research.add_argument("--temperature", type=float, default=0.0)
    research.add_argument("--plan", action="store_true", help="create an execution plan without API calls")
    research.add_argument("--max-candidates", type=int, default=None)
    research.add_argument("--max-agent-calls", type=int, default=None)
    research.add_argument("--max-llm-calls", type=int, default=None)
    research.add_argument("--max-output-tokens-per-call", type=int, default=None)
    research.add_argument("--max-total-completed-tokens", type=int, default=None)
    args = parser.parse_args()
    if args.command == "research":
        requested_run_id = validate_run_id(args.run_id)
        cache_root = stable_cache_root()
        verified_manifest = verify_run_manifest(root=cache_root, run_id=requested_run_id)
        queue_path = safe_artifact_path(cache_root, "research_runs", requested_run_id, "research_queue.json")
        payload = json.loads(queue_path.read_text(encoding="utf-8"))
        if payload.get("run_id") != requested_run_id or payload.get("payload", {}).get("run_id") != requested_run_id:
            raise SystemExit("research queue run_id does not match requested run_id")
        if payload.get("methodology_version") != verified_manifest.methodology_version:
            raise SystemExit("research queue methodology does not match committed manifest")
        entries = payload["payload"]["entries"]
        requested_tickers = list(args.tickers)
        if args.ticker:
            requested_tickers.append(args.ticker)
        if args.plan or args.execute_llm or args.pareto_front is not None or args.tickers:
            # Use a lightweight queue view: scheduler requires only entries' fields.
            from types import SimpleNamespace
            queue_obj = SimpleNamespace(run_id=payload["run_id"], universe=payload["universe"], entries=tuple(SimpleNamespace(**item) for item in entries))
            model = args.model or os.getenv("OPENAI_MODEL")
            budget = ResearchBudget(max_candidates=args.max_candidates, max_agent_calls=args.max_agent_calls, max_llm_calls=args.max_llm_calls, max_output_tokens_per_call=args.max_output_tokens_per_call, max_total_completed_tokens=args.max_total_completed_tokens, model=model)
            mode = "PARETO_FRONT" if args.pareto_front is not None else "EXPLICIT"
            plan = build_execution_plan(queue_obj, agents=tuple(name.strip().lower() for name in args.agents.split(",") if name.strip()), selection_mode=mode, tickers=tuple(requested_tickers), pareto_front=args.pareto_front, budget=budget)
            if args.plan or not args.execute_llm:
                paths = persist_execution_artifacts(plan, None, root=stable_cache_root())
                print(json.dumps({"run_id": plan.run_id, "mode": "plan", "status": plan.status, "selected_tickers": plan.selected_tickers, "agents_per_ticker": plan.agents_per_ticker, "planned_call_count": plan.planned_call_count, "model": plan.model, "selection_mode": plan.selection_mode, "excluded_candidates": plan.excluded_candidates, "unresolved_selection": plan.unresolved_selection, "paths": paths}, default=str, indent=2))
                return
            report = execute_plan(plan, queue_obj, OpenAIProvider(), temperature=args.temperature, root=stable_cache_root())
            paths = persist_execution_artifacts(plan, report, root=stable_cache_root())
            print(json.dumps({"run_id": report.run_id, "status": report.status, "budget_status": report.budget_status, "model": report.model, "completed_calls": report.usage.completed_calls, "failed_calls": report.usage.failed_calls, "unexecuted_calls": len(report.unexecuted_calls), "paths": paths}, default=str, indent=2))
            return
        if not args.ticker:
            raise SystemExit("research requires --ticker, --tickers, --pareto-front, or --plan")
        entry = next((item for item in entries if item.get("ticker") == args.ticker), None)
        if entry is None:
            raise SystemExit(f"ticker not found in ResearchQueue: {args.ticker}")
        package = entry["research_package"]
        runtime = InvestorAgentRuntime(OpenAIProvider(), model=args.model, temperature=args.temperature)
        agents = tuple(name.strip().lower() for name in args.agents.split(",") if name.strip())
        if not args.execute_llm or args.dry_run:
            print(json.dumps({"run_id": args.run_id, "ticker": args.ticker, "mode": "dry-run", "prompts": [runtime.preview(package, name).__dict__ for name in agents]}, default=str, indent=2))
            return
        results = []
        for name in agents:
            result = runtime.analyze(package, name, source_run_id=args.run_id)
            path = persist_investor_result(result, root=stable_cache_root(), source_run_id=args.run_id)
            results.append({"agent": name, "status": result.status.value, "path": str(path), "warnings": result.warnings, "usage": result.usage})
        print(json.dumps({"run_id": args.run_id, "ticker": args.ticker, "results": results}, default=str, indent=2))
        return
    provider = StaticUniverseProvider(args.tickers) if args.universe == "custom" else SP500UniverseProvider()
    result = CandidateScanner(provider, TradingAgentsFundamentalsAdapter()).scan(limit=args.limit)
    payload = {
        "universe": result.universe, "universe_size": result.universe_size,
        "attempted": result.attempted, "successfully_analyzed": result.successfully_analyzed,
        "qualified": result.qualified, "qualified_with_warnings": result.qualified_with_warnings,
        "not_qualified": result.not_qualified, "insufficient_data": result.insufficient_data,
        "provider_failures": result.provider_failures, "status": result.status,
        "qualified_tickers": result.qualified_tickers,
        "records": [{"ticker": r.ticker, "scan_status": r.scan_status.value,
                     "qualification_status": r.qualification_status.value if r.qualification_status else None,
                     "data_status": r.data_status, "warnings_count": r.warnings_count,
                     "failure_reason": r.failure_reason} for r in result.records],
    }
    ranking = None
    if args.rank or args.export_research:
        ranking = rank_scan_result(result)
        payload["ranking"] = {
            "status": ranking.status,
            "input_candidate_count": ranking.input_candidate_count,
            "rankable_count": ranking.rankable_count,
            "unranked_count": ranking.unranked_count,
            "fronts": [
                [{"ticker": c.ticker, "pareto_front": c.pareto_front,
                  "qualification_status": c.qualification_status.value,
                  "piotroski_score": c.piotroski_score, "normalized_pr": c.normalized_pr,
                  "caveats": c.caveats} for c in front]
                for front in ranking.fronts
            ],
            "unranked_candidates": [
                {"ticker": c.ticker, "reason": c.reason,
                 "qualification_status": c.qualification_status.value if c.qualification_status else None,
                 "missing_metrics": c.missing_metrics}
                for c in ranking.unranked_candidates
            ],
        }
    if args.export_research:
        queue = build_research_queue(result, ranking)
        paths = export_research_run(result, ranking, queue)
        payload["research_queue"] = {
            "status": queue.status,
            "run_id": queue.run_id,
            "pareto_ranked_count": queue.pareto_ranked_count,
            "qualified_unranked_count": queue.qualified_unranked_count,
            "total": len(queue.entries),
            "methodology_version": queue.methodology_version,
            "paths": paths,
        }
    print(json.dumps(payload, default=str, indent=2))


if __name__ == "__main__":
    main()
