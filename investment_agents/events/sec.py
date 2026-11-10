"""SEC EDGAR JSON evidence adapter (metadata only, no filing conclusions)."""

from __future__ import annotations
from datetime import date, datetime, timezone
import os
import re
import time
from typing import Any, Callable, Mapping
import requests

from .models import EventEvidence, EvidenceSourceClass


_OFFICIAL_8K_ITEM = re.compile(r"^[1-9]\.[0-9]{2}$")


def _parse_official_item_codes(value: Any) -> tuple[str, ...]:
    """Parse only SEC submissions `items` metadata; never infer from prose."""
    if not isinstance(value, str):
        return ()
    return tuple(
        code for code in (part.strip() for part in value.split(","))
        if _OFFICIAL_8K_ITEM.fullmatch(code)
    )


class SECEdgarAdapter:
    company_tickers_url = "https://www.sec.gov/files/company_tickers.json"
    submissions_url = "https://data.sec.gov/submissions/CIK{cik:010d}.json"

    def __init__(self, *, user_agent: str | None = None, get: Callable[..., Any] | None = None) -> None:
        self.user_agent = os.getenv("SEC_USER_AGENT") if user_agent is None else user_agent
        self._get = get or requests.get

    def _json(self, url: str) -> Mapping[str, Any]:
        """Fetch one SEC JSON resource with bounded, classified retries."""
        last_status = None
        for attempt in range(2):
            response = self._get(url, headers={"User-Agent": self.user_agent}, timeout=20)
            last_status = getattr(response, "status_code", None)
            if last_status == 429:
                if attempt == 0:
                    time.sleep(0.2)
                    continue
                raise RuntimeError("SEC_RATE_LIMIT_429")
            if last_status == 403:
                raise RuntimeError("SEC_FORBIDDEN_403")
            if isinstance(last_status, int) and last_status >= 500:
                if attempt == 0:
                    time.sleep(0.2)
                    continue
                raise RuntimeError("SEC_SERVER_ERROR_5XX")
            response.raise_for_status()
            return response.json()
        raise RuntimeError(f"SEC_REQUEST_FAILED_{last_status}")

    def get_cik_map(self) -> dict[str, str]:
        if not self.user_agent:
            raise RuntimeError("SEC_PROVIDER_NOT_CONFIGURED: set SEC_USER_AGENT")
        payload = self._json(self.company_tickers_url)
        return {str(item["ticker"]).upper(): str(item["cik_str"]).zfill(10) for item in payload.values() if item.get("ticker") and item.get("cik_str") is not None}

    def fetch_filings(self, ticker: str, *, start: date, end: date) -> tuple[EventEvidence, ...]:
        cik = self.get_cik_map().get(ticker.upper())
        if cik is None:
            raise ValueError(f"SEC ticker CIK not found: {ticker}")
        recent = self._json(self.submissions_url.format(cik=int(cik))).get("filings", {}).get("recent", {})
        output: list[EventEvidence] = []
        def at(name: str, index: int) -> Any:
            values = recent.get(name, ())
            return values[index] if isinstance(values, (list, tuple)) and index < len(values) else None

        for index, form in enumerate(recent.get("form", ())):
            if form not in {"10-K", "10-Q", "8-K", "6-K"}:
                continue
            filing_value = at("filingDate", index)
            if not filing_value:
                continue
            filed = date.fromisoformat(filing_value)
            if not start <= filed <= end:
                continue
            accession = at("accessionNumber", index)
            if not accession:
                continue
            primary = at("primaryDocument", index)
            report_date = at("reportDate", index)
            items_raw = at("items", index)
            item_codes = _parse_official_item_codes(items_raw) if form == "8-K" else ()
            warnings = ("SEC_8K_ITEM_CODES_UNAVAILABLE",) if form == "8-K" and not item_codes else ()
            output.append(EventEvidence(
                evidence_id=f"sec:{accession}", ticker=ticker.upper(), evidence_type=form,
                source_class=EvidenceSourceClass.PRIMARY_CORPORATE, source="SEC EDGAR",
                title=form, observed_at=date.fromisoformat(report_date) if report_date else None,
                published_at=filed, fetched_at=datetime.now(timezone.utc),
                reference=f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/{accession.replace('-', '')}/{primary}" if primary else None,
                raw_metadata={
                    "cik": cik,
                    "accession_number": accession,
                    "primary_document": primary,
                    "report_date": report_date,
                    "official_item_codes": item_codes,
                    "items_raw": items_raw,
                    "structured_item_metadata_complete": form != "8-K" or bool(item_codes),
                    "automated_materiality_eligible": False,
                },
                provenance={"endpoint": self.submissions_url.format(cik=int(cik)), "user_agent_configured": True},
                warnings=warnings,
            ))
        return tuple(output)
