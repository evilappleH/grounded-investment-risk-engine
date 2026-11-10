# Step 8A Event Evidence

Event evidence is a provenance-preserving context layer, not a score or
recommendation. `EventEvidencePackage` uses an explicit configurable lookback
and remains `CURRENT_ANALYSIS` only (`pit_safe=false`).

`SECEdgarAdapter` uses the SEC JSON company-tickers and submissions endpoints,
requires `SEC_USER_AGENT`, and records filing metadata only (10-K, 10-Q, 8-K,
6-K). TradingAgents news, FRED macro, and Reddit/StockTwits sentiment are thin
bridges. Their current repository interfaces return formatted text rather than
stable article/observation objects in several paths; those paths are reported
as schema-unstructured instead of being parsed heuristically.

Provider failures are isolated from `NO_EVIDENCE_FOUND`. Duplicate references
are retained and marked `POSSIBLE_DUPLICATE`; no fuzzy title deletion is used.
No event source receives a numeric trust or investment weight.
