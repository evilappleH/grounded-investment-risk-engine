"""Offline tests for Step 6C research lanes and persistence."""

from datetime import datetime, timezone
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from investment_agents.qualification import QualificationStatus
from investment_agents.ranking import rank_scan_result
from investment_agents.research import build_research_queue, export_research_run
from investment_agents.scanner import ScanRecord, ScanResult, ScanStatus


def record(ticker, qualification, *, complete=True, pr=0.6, warnings=("DATA_BASIS_MISMATCH",)):
    return ScanRecord(
        ticker=ticker, company_name=f"{ticker} Co", sector="Technology", industry="Software",
        scan_status=ScanStatus.ANALYZED, qualification_status=qualification, data_status="ANALYZED",
        pr_summary={"pr": pr, "status": "PR_AVAILABLE" if pr is not None else "PR_UNAVAILABLE", "warnings": warnings},
        piotroski_summary={"status": "COMPLETE" if complete else "PARTIAL", "score": 6 if complete else None, "partial_score": 5 if not complete else None, "missing_components": () if complete else ("F_EQOFFER",)},
        warnings_count=1, strong_warnings=(), failure_reason=None, retrieved_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )


def scan(*records, run_id="run-test"):
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    return ScanResult("custom", len(records), len(records), len(records), 0, 0, 0, 0, 0, now, now, tuple(records), "QUALIFIED_CANDIDATES_AVAILABLE", {}, run_id=run_id)


class ResearchTests(unittest.TestCase):
    def test_two_lanes_and_exclusions(self):
        current = scan(record("A", QualificationStatus.QUALIFIED), record("B", QualificationStatus.QUALIFIED_WITH_WARNINGS, complete=False), record("C", QualificationStatus.NOT_QUALIFIED))
        queue = build_research_queue(current, rank_scan_result(current))
        self.assertEqual([entry.lane for entry in queue.entries], ["PARETO_RANKED", "QUALIFIED_EVIDENCE_COMPLETE_UNRANKED"])
        self.assertEqual(queue.entries[1].unranked_reason, "UNRANKED_DIAGNOSTIC_INCOMPLETE")
        self.assertEqual(queue.excluded_nonqualified_count, 1)
        self.assertIsNone(queue.entries[1].pareto_front)

    def test_pr_unavailable_is_qualified_unranked(self):
        current = scan(record("A", QualificationStatus.QUALIFIED, pr=None))
        queue = build_research_queue(current, rank_scan_result(current))
        self.assertEqual(queue.status, "RESEARCH_CANDIDATES_AVAILABLE")
        self.assertEqual(queue.entries[0].lane, "QUALIFIED_EVIDENCE_COMPLETE_UNRANKED")
        self.assertEqual(queue.entries[0].unranked_reason, "UNRANKED_VALUATION_UNAVAILABLE")

    def test_warnings_and_partial_score_are_preserved(self):
        current = scan(record("A", QualificationStatus.QUALIFIED_WITH_WARNINGS, complete=False))
        queue = build_research_queue(current, rank_scan_result(current))
        entry = queue.entries[0]
        self.assertEqual(entry.partial_score, 5)
        self.assertIsNone(entry.official_piotroski_score)
        self.assertEqual(entry.pr_warnings, ("DATA_BASIS_MISMATCH",))

    def test_persistence_run_id_round_trip_and_snapshot_not_pit(self):
        current = scan(record("A", QualificationStatus.QUALIFIED), run_id="stable-run")
        ranking = rank_scan_result(current)
        queue = build_research_queue(current, ranking)
        with TemporaryDirectory() as temp:
            paths = export_research_run(current, ranking, queue, root=Path(temp))
            self.assertTrue(Path(paths["universe_snapshot"]).exists())
            snapshot = json.loads(Path(paths["universe_snapshot"]).read_text(encoding="utf-8"))
            report = json.loads(Path(paths["research_queue"]).read_text(encoding="utf-8"))
            self.assertFalse(snapshot["pit_safe"])
            self.assertEqual(snapshot["run_id"], "stable-run")
            self.assertEqual(report["run_id"], "stable-run")
            with self.assertRaises(FileExistsError):
                export_research_run(current, ranking, queue, root=Path(temp))

    def test_zero_candidates_and_zero_rankable(self):
        empty = scan()
        empty = ScanResult(empty.universe, 0, 0, 0, 0, 0, 0, 0, 0, empty.started_at, empty.completed_at, (), "NO_QUALIFIED_CANDIDATE", {}, run_id="empty")
        queue = build_research_queue(empty, rank_scan_result(empty))
        self.assertEqual(queue.status, "NO_RESEARCH_CANDIDATE")
        partial = scan(record("A", QualificationStatus.QUALIFIED, complete=False))
        partial_queue = build_research_queue(partial, rank_scan_result(partial))
        self.assertEqual(partial_queue.pareto_ranked_count, 0)
        self.assertEqual(partial_queue.qualified_unranked_count, 1)


if __name__ == "__main__":
    unittest.main()
