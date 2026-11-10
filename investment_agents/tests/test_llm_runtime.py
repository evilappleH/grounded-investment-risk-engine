"""Offline tests for Step 7A provider/runtime boundaries."""

from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from investment_agents.llm import (
    InvestorAgentRuntime, InvestorResearchResult, LLMProvider, LLMProviderNotConfigured,
    LLMResponse, OpenAIProvider, ResearchResultStatus, persist_investor_result,
)
from investment_agents.llm.provider import LLMProviderFailure, classify_provider_exception


PACKAGE = {
    "ticker": "AAPL", "company_name": "Example", "sector": "Technology", "industry": "Software",
    "current_fundamentals": {"revenue": 100, "net_income": 20, "free_cash_flow": 15, "operating_cash_flow": 22, "equity": 50, "total_debt": 30, "cash": 10, "pe": 15, "pb": 2, "roe_percent": 15, "dividend_payout_percent": 50},
    "historical_fundamentals": [{"report_period": "2025-12-31"}],
    "valuation": {"pr": 1.0, "warnings": ["ROE_BASIS_VENDOR_DEFINED"], "pr_corrected_experimental": True},
    "financial_diagnostics": {"warnings": ["NET_DEBT_POSITIVE"]},
    "piotroski": {"status": "PARTIAL", "score": None, "partial_score": 7, "warnings": ["F_EQOFFER"]},
    "qualification": {"status": "QUALIFIED_WITH_WARNINGS"},
    "ranking_context": {"lane": "QUALIFIED_UNRANKED", "unranked_reason": "UNRANKED_DIAGNOSTIC_INCOMPLETE"},
    "provenance": {"analysis_mode": "CURRENT_ANALYSIS", "methodology_version": "step6c-v1", "filing_time_unavailable": True, "available_time_unavailable": True},
}

VALID = '{"thesis_summary":"Mixed evidence","strengths":["cash flow"],"concerns":["partial data"],"valuation_view":"unknown","financial_quality_view":"mixed","key_unknowns":["filing date"],"data_limitations":["CURRENT_ANALYSIS not PIT-safe"],"conditions_that_would_change_view":["complete history"],"research_status":"MIXED","confidence":55}'


class FakeProvider(LLMProvider):
    provider_name = "fake"
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = 0
    def generate(self, *, system_prompt, user_prompt, model, temperature=None):
        self.calls += 1
        response = self.responses.pop(0)
        return LLMResponse(response, "fake", model, {"input_tokens": 10, "output_tokens": 5, "total_tokens": 15})


class RuntimeTests(unittest.TestCase):
    def test_dry_run_zero_calls_and_prompt_boundary(self):
        provider = FakeProvider([VALID])
        runtime = InvestorAgentRuntime(provider, model="test")
        preview = runtime.preview(PACKAGE, "buffett")
        self.assertEqual(provider.calls, 0)
        self.assertIn("ROE_BASIS_VENDOR_DEFINED", preview.user_prompt)
        self.assertIn("PARTIAL", preview.user_prompt)
        self.assertIn("CURRENT_ANALYSIS", preview.user_prompt)
        self.assertNotEqual(preview.system_prompt, runtime.preview(PACKAGE, "munger").system_prompt)

    def test_structured_parse_usage_and_unverified_claim(self):
        provider = FakeProvider([VALID.replace("Mixed evidence", "Mixed evidence with 9999")])
        result = InvestorAgentRuntime(provider, model="test").analyze(PACKAGE, "buffett", source_run_id="r1")
        self.assertEqual(result.status, ResearchResultStatus.COMPLETE)
        self.assertIn("UNVERIFIED_MODEL_CLAIM", result.warnings)
        self.assertEqual(result.usage.total_tokens, 15)

    def test_malformed_json_bounded_retry(self):
        provider = FakeProvider(["not json", VALID])
        result = InvestorAgentRuntime(provider, model="test", max_retries=1).analyze(PACKAGE, "munger")
        self.assertEqual(result.status, ResearchResultStatus.COMPLETE)
        self.assertEqual(provider.calls, 2)
        provider = FakeProvider(["not json", VALID, VALID])
        result = InvestorAgentRuntime(provider, model="test", max_retries=0).analyze(PACKAGE, "munger")
        self.assertEqual(result.status, ResearchResultStatus.PARSE_ERROR)
        self.assertEqual(provider.calls, 1)

    def test_missing_api_key_safe_failure(self):
        with patch.dict("os.environ", {"OPENAI_API_KEY": ""}):
            result = InvestorAgentRuntime(OpenAIProvider(api_key=None), model="test").analyze(PACKAGE, "buffett")
        self.assertEqual(result.status, ResearchResultStatus.LLM_PROVIDER_NOT_CONFIGURED)

    def test_persistence_has_no_secret_or_trade_fields(self):
        result = InvestorAgentRuntime(FakeProvider([VALID]), model="test").analyze(PACKAGE, "buffett", source_run_id="r1")
        with TemporaryDirectory() as temp:
            path = persist_investor_result(result, root=Path(temp), source_run_id="r1")
            text = path.read_text(encoding="utf-8")
            self.assertNotIn("OPENAI_API_KEY", text)
            self.assertNotIn("position_size", text)
            self.assertNotIn("target_price", text)

    def test_sanitized_provider_failure_classification(self):
        class E(Exception):
            status_code = 401
            body = {"error": {"type": "invalid_request_error", "code": "invalid_api_key"}}
        failure = classify_provider_exception(E(), model="test-model")
        self.assertEqual(failure.code, "OPENAI_AUTH_ERROR")
        self.assertFalse(failure.retryable)
        self.assertNotIn("key", str(failure).lower())

    def test_runtime_persists_sanitized_failure_metadata(self):
        class Failing(LLMProvider):
            provider_name = "fake"
            def generate(self, **kwargs):
                raise LLMProviderFailure("OPENAI_CREDIT_BALANCE_EXHAUSTED", http_status=429, model=kwargs["model"])
        result = InvestorAgentRuntime(Failing(), model="test").analyze(PACKAGE, "buffett")
        self.assertEqual(result.error_type, "OPENAI_CREDIT_BALANCE_EXHAUSTED")
        self.assertEqual(result.raw_response_metadata["http_status"], 429)

    def test_bad_request_is_schema_error(self):
        class E(Exception):
            status_code = 400
            body = {"error": {"type": "invalid_request_error", "code": "invalid_request"}}
        self.assertEqual(classify_provider_exception(E(), model="test").code, "OPENAI_SCHEMA_ERROR")


if __name__ == "__main__":
    unittest.main()
