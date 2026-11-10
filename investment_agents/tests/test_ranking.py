"""Offline tests for Step 6B's unweighted Pareto research fronts."""

from datetime import datetime, timezone
import unittest

from investment_agents.qualification import QualificationStatus
from investment_agents.ranking import rank_scan_result
from investment_agents.scanner import ScanRecord, ScanResult, ScanStatus


def rec(ticker, score=None, pr=None, *, qualification=QualificationStatus.QUALIFIED_WITH_WARNINGS,
        pi_status=None, pr_status=None, warnings=()):
    return ScanRecord(
        ticker=ticker, company_name=None, sector=None, scan_status=ScanStatus.ANALYZED,
        qualification_status=qualification, data_status="ANALYZED",
        pr_summary={"pr": pr, "status": pr_status or ("PR_AVAILABLE" if pr is not None else "PR_UNAVAILABLE"), "warnings": warnings},
        piotroski_summary={"status": pi_status or ("COMPLETE" if score is not None else "PARTIAL"), "score": score},
        warnings_count=0, strong_warnings=(), failure_reason=None, retrieved_at=None,
    )


def result(*records):
    now = datetime.now(timezone.utc)
    return ScanResult("custom", len(records), len(records), len(records), 0, 0, 0, 0, 0, now, now, tuple(records), "QUALIFIED_CANDIDATES_AVAILABLE", {}, qualified_tickers=tuple(r.ticker for r in records))


class RankingTests(unittest.TestCase):
    def test_exclusion_and_partial_and_missing_pr(self):
        ranked = rank_scan_result(result(
            rec("A", 8, 0.8), rec("B", 7, 0.5), rec("C", None, 0.4),
            rec("D", 9, None), rec("E", 9, 0.7, qualification=QualificationStatus.NOT_QUALIFIED),
        ))
        self.assertEqual(ranked.rankable_count, 2)
        self.assertEqual({x.reason for x in ranked.unranked_candidates}, {"UNRANKED_DIAGNOSTIC_INCOMPLETE", "UNRANKED_VALUATION_UNAVAILABLE", "QUALIFICATION_NOT_ELIGIBLE"})

    def test_dominance_and_multiple_fronts(self):
        ranked = rank_scan_result(result(rec("A", 9, 0.5), rec("B", 8, 0.6), rec("C", 7, 0.8)))
        self.assertEqual([[c.ticker for c in front] for front in ranked.fronts], [["A"], ["B"], ["C"]])

    def test_tradeoff_same_front_and_ties(self):
        ranked = rank_scan_result(result(rec("Z", 9, 0.8), rec("A", 8, 0.5), rec("B", 8, 0.5)))
        self.assertEqual([[c.ticker for c in front] for front in ranked.fronts], [["A", "B", "Z"]])

    def test_alphabetical_is_display_only_and_warnings_do_not_change_math(self):
        ranked = rank_scan_result(result(rec("B", 9, 0.5, warnings=("DATA_BASIS_MISMATCH",)), rec("A", 9, 0.5)))
        self.assertEqual([c.ticker for c in ranked.fronts[0]], ["A", "B"])
        self.assertEqual(ranked.fronts[0][1].caveats, ("DATA_BASIS_MISMATCH",))

    def test_no_corrected_or_cycle_dimensions_and_no_forced_rank(self):
        item = rec("A", 8, 0.6)
        item.pr_summary["pr_corrected"] = 0.01
        item.pr_summary["pr_cycle"] = 99
        ranked = rank_scan_result(result(item))
        self.assertEqual(ranked.status, "RANKABLE_CANDIDATES_AVAILABLE")
        self.assertFalse(hasattr(ranked.fronts[0][0], "total_score"))
        self.assertFalse(hasattr(ranked.fronts[0][0], "weighted_score"))

    def test_zero_rankable(self):
        ranked = rank_scan_result(result(rec("A", None, None)))
        self.assertEqual(ranked.status, "NO_RANKABLE_CANDIDATE")
        self.assertEqual(ranked.rankable_count, 0)


if __name__ == "__main__":
    unittest.main()
