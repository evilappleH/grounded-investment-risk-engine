"""Pure eligibility gate for the one-share pre-auto paper test.

This gate never connects to Moomoo and cannot create an order. It only proves
that an already policy-approved test intent satisfies the hard test limits.
"""
from __future__ import annotations
from dataclasses import dataclass


@dataclass(frozen=True)
class PreAutoPaperTestDecision:
    eligible: bool
    reasons: tuple[str, ...]
    notional: float


def evaluate_pre_auto_paper_test(*, acc_id: int, expected_acc_id: int | None, trd_env: str, market: str, quantity: float, limit_price: float, quote_fresh: bool, risk_pass: bool, policy_pass: bool, duplicate: bool = False) -> PreAutoPaperTestDecision:
    reasons: list[str] = []
    notional = float(quantity) * float(limit_price)
    if expected_acc_id is None or acc_id != expected_acc_id or trd_env != "SIMULATE" or market != "US": reasons.append("MOOMOO_REAL_EXECUTION_FORBIDDEN")
    if quantity != 1 or quantity <= 0 or int(quantity) != quantity: reasons.append("ONE_WHOLE_SHARE_ONLY")
    if notional > 500: reasons.append("TEST_NOTIONAL_EXCEEDS_500_USD")
    if not quote_fresh: reasons.append("STALE_QUOTE")
    if not policy_pass: reasons.append("PORTFOLIO_POLICY_NOT_ELIGIBLE")
    if not risk_pass: reasons.append("RISK_GATE_BLOCKED")
    if duplicate: reasons.append("DUPLICATE_TEST_ORDER")
    return PreAutoPaperTestDecision(eligible=not reasons, reasons=tuple(dict.fromkeys(reasons)), notional=notional)
