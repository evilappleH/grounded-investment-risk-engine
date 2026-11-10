# Financial diagnostics

`diagnose_financials()` describes facts from one `FundamentalRecord`. It
computes only well-defined ratios, preserves missing values, and emits
data-quality/fact warnings. It never emits BUY, SELL, REJECT, scores, or
qualification decisions. An optional `PRMetrics` object is retained as
structured context and its warnings are propagated without changing PR.

`calculate_piotroski_score()` is separate and implements the nine original
binary signals from Joseph D. Piotroski (2000), DOI `10.2307/2672906`.
The original study used Compustat annual data for high book-to-market firms;
this adapter uses structured yfinance annual statements in CURRENT_ANALYSIS
only. Missing filing/availability dates, vendor row differences, and the lack
of a confirmed common-equity issuance row are reported rather than guessed.

Formal `F_DELTA_TURN` follows Piotroski's average-total-assets definition and is
versioned as `piotroski-2000-average-assets-v2`. The former beginning-assets
calculation is available only through the clearly named legacy comparison API;
it returns no official score and cannot enter Pareto ranking.
