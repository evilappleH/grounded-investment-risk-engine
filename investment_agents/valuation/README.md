# Profit-Ratio (PR) factor

PR is a custom project valuation factor. It is inspired only in the broad sense
of evaluating business quality with price; it is **not** represented as a
Warren Buffett or Charlie Munger invention or historical method.

`roe_percent` and `dividend_payout_percent` are internal percentage-number
fields: pass `15.0` for 15%, `1.0` for 1%, and `0.8` for 0.8%. The PR module
does not infer units from magnitude or convert decimal-provider values; a future
data adapter must normalize an external decimal such as `0.15` before calling it.

```text
PR_from_PE_ROE = PE / ROE_percent
PR_from_PB_ROE = PB * 100 / ROE_percent²
PR_corrected = N * PR
pr_original_scale = 100 * PR
```

The formal project metric is normalized `PR = PE_TTM / ROE_pct`. Thus PE=15
and ROE_pct=15 produce PR=1.0. `pr_original_scale` is retained only for
compatibility/reference and is exactly 100 times normalized PR; it is never the
main PR, a qualification input, or a threshold scale. The two scales must not
be mixed.

The two raw PR expressions are theoretically equivalent only when PE, PB, and
ROE use consistent accounting and time bases. Real vendor snapshots often do
not; that case produces `DATA_BASIS_MISMATCH` and does not reject or overwrite
either value.

The optional payout multiplier is `N=2` at or below 25%, falls linearly to
`N=1` at 50%, and remains `1` above 50%. Missing payout leaves
`PR_corrected` unavailable; no multiplier is assumed. `ttm`, `3y_average`,
and `5y_average` are input labels only—this module does not identify cyclical
companies.

Interpretation applies only to normalized PR: PR<1 is relatively low under this
project reference model, PR≈1 is PE/ROE baseline matching, and PR>1 is relatively high.
The `0.4–0.6` interval is only `project_reference_band`; it is not a
guaranteed buy zone, intrinsic value, or an official Buffett threshold.

`PR_AVAILABLE` is an analytical availability status, not a ranking, BUY, SELL,
WATCH, recommendation, portfolio rule, or trade instruction. A value near 1
can be read as valuation roughly matching ROE, below 1 as relatively cheaper,
and above 1 as relatively more expensive. The 0.4–0.6 band is retained only
as a future research hypothesis, not a forced buy threshold. PR is unavailable
for non-positive PE/ROE, known earnings losses, and missing required inputs.

`PR_corrected` has `experimental=true`. It does not participate in qualification, is
not a hard buy threshold, and never replaces raw PR. It requires future
backtest validation.

Important limitations: leverage can inflate ROE; ROE is not cash flow; cycle
peaks can make PR deceptively low; PE/ROE depend on accounting quality; and PR
does not apply to loss-making companies.

# Profit-Ratio

`calculate_pr_cycle()` formally computes the normalized PB-based metric
`PB_current * 100 / ROE_avg_pct**2` from an explicitly supplied real 3Y or 5Y
average ROE and current PB. It never classifies a company as cyclical or picks
a window. `PE_TTM / ROE_avg_pct` is exposed only as
`pr_cycle_pe_reference`; it is not an equivalent cross-check. If the requested
average or current PB is unavailable, it returns `PR_CYCLE_NOT_COMPUTED`.
Current-analysis annual records remain unsuitable for BACKTEST_PIT because
filing and availability timestamps are absent.
