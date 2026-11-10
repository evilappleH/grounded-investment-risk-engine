# Data and Universe contracts (Step 4A)

`investment_agents` owns vendor-neutral contracts. Investor personas, PR, and
future scanner code must depend on these contracts, never directly on yfinance,
Alpha Vantage, or another vendor.

```text
investment_agents
  -> MarketDataProvider / FundamentalsProvider / UniverseProvider
  -> future adapter
  -> vendor or TradingAgents dataflows
```

The default requested history window is five years of daily prices and five
years of annual/quarterly fundamentals. This is a request convention only:
coverage is reported as `FULL_HISTORY` (at least five calendar years),
`LIMITED_HISTORY` (three to under five), or `INSUFFICIENT_HISTORY` (under three).
No status automatically admits, rejects, ranks, or removes a ticker.

`FundamentalRecord` reserves `report_period`, `filing_date`, `published_at`,
`available_at`, and `source`. Historical use is legal only when
`available_at <= analysis_as_of`. For example, a 2024-12-31 report filed on
2025-02-20 must not be used by an analysis dated 2024-12-31.

Internal percent fields are always `roe_percent` and
`dividend_payout_percent`: 15% is `15.0`. A future data adapter, rather than PR,
must convert vendor decimal values such as `0.15` before constructing a record.

`StaticUniverseProvider` is the only concrete provider in Step 4A. It supports
offline custom US-equities ticker lists, normalizes case/whitespace, removes
duplicates, and rejects malformed tickers. `sp_500`, `nasdaq_100`, and
`russell_1000` are declared universe kinds only; no constituent download exists.

`TradingAgentsMarketDataAdapter` is active for daily prices: it lazily calls the
existing TradingAgents `load_ohlcv()` function, preserving its yfinance cache,
configured `data_cache_dir`, and date filtering. It adds no second cache.

Fundamentals modes are intentionally isolated:

- `CURRENT_ANALYSIS` uses native yfinance `Ticker.info` (dict) and annual
  `financials`, `cashflow`, and `balance_sheet` DataFrames via the provider
  adapter. It records `source` and `retrieved_at`, but does not treat these as
  PIT-safe because yfinance does not provide dependable availability dates here.
- `BACKTEST_PIT` requires actual report/filing/publication/availability data.
  It currently returns `PIT_DATA_UNAVAILABLE`; it never falls back to current
  data and never estimates `available_at` from `report_period`.

`TradingAgentsFundamentalsAdapter` does not parse existing TradingAgents prose or
CSV via regular expressions. It reuses the native structured yfinance objects
that TradingAgents' presentation functions consume, while keeping all direct
vendor access inside the adapter boundary.
# Data boundaries

The TradingAgents fundamentals adapter exposes yfinance structured annual
statements as five analysis-year `FundamentalRecord` values for
`CURRENT_ANALYSIS`, while retaining raw period metadata and (when available)
one supporting prior-year balance-sheet period for average-equity ROE.
Each record retains its own report period, source, retrieval time, and field
metadata. Historical records have no reliable filing/publication/availability
timestamps and are therefore not PIT-safe; `BACKTEST_PIT` remains fail closed.

Historical annual ROE is calculated as
`net_income / ((equity_t + equity_(t-1)) / 2) * 100`. Missing prior-year
equity leaves that year's ROE unavailable. Three- and five-year averages are
reported only when exactly at least three/five valid annual ROEs exist; no
shorter window is substituted.

Annual records also preserve raw statement fields for future diagnostics:
total/current assets, current liabilities, long-term debt, operating cash
flow, gross profit, operating income, and diluted average shares. Shares keep
an explicit basis in field metadata; no shares field is silently substituted.
