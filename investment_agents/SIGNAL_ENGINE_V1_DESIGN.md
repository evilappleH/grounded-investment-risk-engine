# Grounded Multi-Agent Investment Signal Engine V1

Status: Phase 1 design complete; Slice A contracts and fail-closed PIT gates implemented  
Scope: audit, contracts, data gates, validation design, and minimum implementation plan  
Safety boundary: risk-first decision support; execution interfaces may be designed
and tested against fakes/simulation, but Phase 1 performs no broker writes and
enables no real-money automation

## 1. Product decision

V1 is a **risk-first Grounded Multi-Agent Investment Signal Engine**. Its primary
purpose is to prevent unsupported, oversized, stale, poorly calibrated, or
policy-violating decisions. Return prediction is a risk input and decision aid,
not the product objective and not an optimization mandate. It combines two deliberately
separate questions:

1. **Long-term thesis:** Is the company sufficiently high quality and reasonably
   valued to own or continue researching?
2. **Timing:** Conditional on information available at an as-of timestamp, what
   is the calibrated probability that the stock will outperform its declared
   benchmark over the next 20 or 60 trading sessions?

The first question is answered by grounded fundamentals, valuation,
Buffett/Munger research, canonical claims, and deterministic policy. The second
is answered by versioned numerical features and fitted statistical models. An
LLM may structure event evidence and explain already-computed results; it must
not create a probability, expected return, model feature value, or backtest
metric.

The eventual system may expose an automatic-execution interface, but execution
is downstream of the signal engine and subordinate to deterministic risk
controls. A signal can never authorize itself. The interface must require a
separate, immutable authorization artifact produced after portfolio limits,
data freshness, model validity, position limits, liquidity, order sanity,
kill-switch, account/environment, and reconciliation checks pass. Phase 1 only
specifies this boundary; it does not arm or call a broker.

Until the dataset gates in section 9 pass, any model output must be labelled
`RESEARCH_ONLY_UNVALIDATED`, and the user-facing probability field must be
absent. A raw classifier score is never presented as a win rate.

## 2. Repository audit

### 2.1 Reusable without architectural change

| Existing capability | Evidence in repository | V1 use |
|---|---|---|
| Daily OHLCV boundary | `data/tradingagents_adapter.py` maps TradingAgents data into sorted, end-date-clipped `DailyPriceBar` records | Input adapter for price/volume features, after adding adjustment and session-quality contracts |
| SEC fundamentals and provenance | `data/sec_provider.py`, `data/models.py`, `data/context.py` | Long-term thesis features only after per-observation availability timestamps are proved |
| Valuation | `valuation/balanced.py`, `valuation/current.py`, `valuation/historical.py`, `valuation/peer.py` | Long-term quality/valuation lane; only PIT-qualified fields may enter historical timing training |
| Grounded personas | `agents/buffett.py`, `agents/munger.py`, `llm/runtime.py` | Explain long-term thesis and invalidation conditions; never emit probability |
| Claim controls | `committee/claims.py`, `committee/agent_v2.py`, `committee/deterministic.py` | Evidence IDs, ticker/run binding, unsupported-claim control, deterministic consensus |
| Deterministic committee | `committee/deterministic.py` | Long-term lane only; preserve disagreement and insufficiency |
| Event evidence boundary | `events/models.py`, `events/bridges.py`, `events/primary.py`, `events/synthesis.py` | Source evidence and provenance foundation for a new typed event-feature contract |
| News/social date guards | upstream tests `test_news_lookahead.py`, `test_social_lookahead.py` | Useful anti-look-ahead checks, but not proof of a historical news dataset |
| Portfolio and risk gates | `portfolio/engine.py`, `risk/gate.py` | Core product controls downstream of research; extend later for signal/model validity, exposure and execution authorization |
| Quote-only monitoring | `price_watch/moomoo_quotes.py`, `price_watch/engine.py` | Later live feature refresh and material-change alerts; keep isolated from trade context |
| Audit artifacts | `research/integrity.py`, persistence/manifest utilities | Dataset/model/report lineage and immutable run references |
| Execution safety | Moomoo/IBKR adapters and tests reject real execution; `PAPER_AUTO` is separately gated | Reuse behind a broker-neutral interface and strict authorization boundary; Phase 1 leaves them untouched |

### 2.2 Reusable only with explicit restrictions

- The TradingAgents OHLCV loader is appropriate for prototyping, but V1 needs a
  declared adjusted-price policy, corporate-action handling, exchange calendar,
  delisting behavior, and reproducible vendor snapshot before metrics are
  publishable.
- Existing event synthesis correctly treats headline-only news as weak evidence
  and currently disables provider-backed interpretation without a typed result
  contract. It does not yet produce the requested event features or historical
  feature snapshots.
- Existing SEC selectors use filing dates in some paths, but the repository's
  own `audit/KNOWN_RISKS.md` says historical fundamentals are not point-in-time
  safe. Current fundamentals, retroactively revised facts, and today's peer set
  must not be joined to old samples.
- News and social sources exclude future-dated items from a requested window.
  Their live/recent APIs do not establish that the same historical corpus was
  available at the old as-of time.
- Current `price_watch` triggers fixed price rules and launches qualitative
  review. It can supply alert/persistence patterns, not the new statistical
  signal itself.
- PortfolioPolicy accepts allocation targets supplied by a caller. It is not a
  learned sizing model and must not be represented as one.

### 2.3 Missing capabilities

There is no current module that provides all of the following:

- immutable sample index keyed by `(instrument_id, as_of_session, horizon)`;
- benchmark and sector membership as known on the sample date;
- adjusted, aligned forward relative-return labels;
- typed event features with availability timestamps;
- price/volume, regime, and sector-relative feature computation;
- purged walk-forward folds;
- fitted Logistic Regression and tree baseline models;
- out-of-fold probability calibration;
- Brier Score, Log Loss, calibration buckets, discrimination metrics, and
  benchmark comparisons;
- model/data cards, dataset fingerprints, or deployability gates;
- a deterministic research-state combiner for `HOLD`, `NO_ADD`,
  `REDUCE_REVIEW`, `SELL_REVIEW`, and `THESIS_INVALIDATED`;
- material probability-change monitoring with feature attribution.

`scikit-learn` is not declared in `pyproject.toml`. Model implementation must be
an optional dependency (proposed extra: `signal = ["scikit-learn>=1.5,<2"]`) or
deferred until dependency policy is approved. Pandas is already available.

## 3. Proposed bounded architecture

Add a new `investment_agents/signal_engine/` package. Do not move or rewrite
existing research, broker, or portfolio modules in V1.

```text
PIT-qualified source snapshots
  -> sample index and availability checks
  -> deterministic feature builders
       price_volume
       market_regime
       sector_relative
       grounded_events
       optional PIT fundamentals/valuation
  -> forward relative-return labels (20D, 60D)
  -> purged walk-forward folds
  -> Logistic Regression + HistGradientBoosting baselines
  -> out-of-fold calibration
  -> frozen model bundle + dataset/model cards
  -> calibrated timing report

grounded long-term research ---------------------> deterministic state combiner
portfolio/position context ----------------------> research state, not an order
```

Suggested modules:

```text
signal_engine/
  contracts.py       # immutable schemas and enums
  availability.py    # PIT checks and fail-closed dataset gate
  labels.py          # session-aligned relative-return labels
  features/
    price_volume.py
    regime.py
    events.py
    fundamentals.py
  datasets.py        # sample construction and fingerprints
  splits.py          # expanding/rolling purged walk-forward folds
  models.py          # sklearn adapters, no LLM dependency
  calibration.py     # OOF calibration and bucket reports
  evaluation.py      # metrics by fold/horizon/regime/sector
  states.py          # deterministic long-term x timing state machine
  reporting.py       # facts, inference, recommendation clearly separated
  monitoring.py      # later: material change detection
```

Neither `signal_engine` nor its CLI may import an official broker adapter.
Execution integration is dependency-inverted: the risk/orchestration layer may
emit a signed/hashed `ExecutionAuthorization`; a separate adapter consumes it.

## 4. Core contracts

Every input record must contain `instrument_id`, `as_of`, `available_at`,
`source`, `source_version`, and `provenance_hash`. A feature is eligible only
when `available_at <= decision_cutoff`. Unknown availability is not equivalent
to historical availability.

### 4.1 Structured event feature record

The event layer stores one record per grounded event cluster rather than free
text as a model feature:

```text
event_id, instrument_id, as_of, available_at
event_type                 # closed, versioned taxonomy
direction                  # POSITIVE/NEGATIVE/MIXED/NEUTRAL/UNKNOWN
sentiment                  # bounded numeric value only if method is versioned
magnitude                  # SMALL/MEDIUM/LARGE/UNKNOWN plus encoded value
horizon                    # IMMEDIATE/20D/60D/LONG_TERM/UNKNOWN
source_quality             # deterministic source-class score
recency_sessions
agreement                  # independent-source agreement, not duplicate count
uncertainty
primary_source_present
full_content_available
evidence_ids
extractor_type             # RULE/LLM_STRUCTURED
extractor_version
warnings
```

The LLM receives untrusted evidence as data and must return the closed schema,
evidence IDs, and uncertainties. A deterministic validator rejects unknown
types, missing evidence, wrong ticker, timestamps after the cutoff, numerical
claims unsupported by evidence, and invalid ranges. Rejected events become
missing/degraded features; they do not silently become neutral.

Aggregate features are computed deterministically at each as-of date, for
example event counts and signed magnitude by 5/20-session window, source-quality
weighted direction, uncertainty, primary-source coverage, and cross-source
agreement. The raw LLM prose is excluded from model inputs.

### 4.2 Timing output

One output per horizon contains:

```text
model_status
horizon_sessions
benchmark_id
as_of_session
calibrated_probability_positive_excess_return
expected_excess_return             # optional; separate regression only
calibration_method/version
validation_window
sample_count and effective_sample_count
brier_score, log_loss
calibration_bucket_reference
feature_snapshot_hash
model_bundle_hash
data_limitations
top_deterministic_drivers
```

If calibration or PIT gates fail, `calibrated_probability...` is null and
`model_status` explains why.

## 5. Labels and benchmark policy

For instrument `i`, sample session `t`, horizon `h` in `{20, 60}`:

```text
r_i(t,h) = adjusted_close_i(t+h) / adjusted_close_i(t) - 1
r_b(t,h) = adjusted_close_b(t+h) / adjusted_close_b(t) - 1
er_i(t,h) = r_i(t,h) - r_b(t,h)
y_i(t,h) = 1 if er_i(t,h) > 0 else 0
```

The primary classifier target is positive benchmark-relative return. Raw excess
return is retained for economic evaluation and a later, explicitly separate
regression baseline. A classifier probability must not be described as the
probability of a specific return magnitude or of avoiding loss.

Rules:

- Use exchange-session indices, never calendar-day offsets.
- Use total-return-compatible adjusted prices for both asset and benchmark, from
  a consistent methodology. Reject mixed adjusted/unadjusted pairs.
- Default benchmark for US equities is a versioned broad-market proxy such as
  SPY total return; sector-relative return is an input/secondary label, not a
  silent replacement. Benchmark selection is stored per sample.
- The entry price convention is fixed before training (V1 proposal: adjusted
  close at `t`; features use information available by that close and the signal
  becomes actionable no earlier than the next session). A future open-to-close
  implementation requires a separate label version.
- Samples without a complete forward horizon remain unlabeled, not negative.
- Delisting/corporate-action coverage is a dataset gate. Dropping failed or
  delisted symbols creates upward bias and is prohibited.
- Because horizons overlap, observations are dependent. Report effective sample
  limitations and use purging/embargo in validation.

## 6. V1 feature set

All rolling fields use data ending at `t`, have documented minimum history, and
emit missingness indicators rather than fabricated zeroes.

### Price and trend

- adjusted returns: 1, 5, 20, and 60 sessions;
- distance to SMA/EMA 20, 60, and 200;
- normalized 20/60-session linear trend slope and fit quality;
- drawdown from rolling 20/60/252-session peak;
- overnight gap and rolling gap statistics.

### Risk and range

- realized volatility at 20 and 60 sessions;
- ATR/price at 14 and 20 sessions;
- downside volatility and downside/upside semivariance ratio;
- optional market beta from trailing data, with minimum-observation gate.

### Volume and participation

- volume / rolling 20- and 60-session median or mean;
- robust volume z-score;
- up-volume/down-volume ratio;
- rolling return-volume correlation and price-volume divergence flags;
- turnover only where PIT shares outstanding or vendor turnover is valid;
- zero/stale volume and corporate-action quality flags.

### Limited technical indicators

- RSI(14);
- MACD line, signal, and histogram with fixed parameters.

These are supporting features, not named trading rules. Avoid a large indicator
zoo and tune no indicator window on the final test period.

### Context

- broad-market 1/5/20/60-session return, trend, volatility, drawdown;
- deterministic regime features (trend above/below 200-day average, volatility
  percentile, drawdown band), stored as inputs rather than hindsight regimes;
- sector ETF 20/60-session return relative to market;
- stock 20/60-session return relative to sector;
- sector membership known at `t`, otherwise `UNKNOWN` and not backfilled.

### Long-term features

Quality and valuation remain a separate report lane by default. A small set may
enter the timing model only after observation-level `available_at` and revision
policy pass the PIT gate. V1 must publish price-only and price-plus-event
ablations before claiming incremental value from fundamentals or agents.

## 7. Models, validation, and calibration

### 7.1 Baselines

For each horizon, compare:

1. prevalence-only constant probability;
2. regularized Logistic Regression with imputation/scaling fitted inside each
   training fold;
3. HistGradientBoostingClassifier if the approved sklearn version supports the
   required pipeline and missing-data behavior.

No deep learning, no random train/test split, and no hyperparameter search on
the final test window. Model selection first prioritizes calibration and stable
out-of-sample behavior, not in-sample accuracy.

### 7.2 Walk-forward design

Use chronological expanding-window folds initially. Each fold has train,
calibration, and test blocks. Fit preprocessing/model on train; fit the
calibrator only on later, disjoint calibration predictions; evaluate once on
the subsequent test block.

Purge training/calibration samples whose forward label interval overlaps the
next block. Apply an embargo of at least the label horizon at fold boundaries
unless a documented interval-based purge proves a smaller embargo sufficient.
Group all records for the same `(ticker, as_of)` together. For a 60D model, the
60D information interval governs the purge even when 20D labels also exist.

The final holdout is the most recent untouched period. In addition to pooled
metrics, report fold, year, sector, market-regime, and liquidity slices with
sample counts. Treat ticker-date rows as a panel; do not claim independence.

### 7.3 Calibration

Candidate methods are sigmoid/Platt and isotonic. Fit only on predictions from
data later than model training and earlier than test. Isotonic is allowed only
above a declared calibration sample threshold; otherwise prefer sigmoid.

Required out-of-sample metrics:

- Brier Score and skill versus prevalence baseline;
- Log Loss;
- calibration intercept/slope where estimable;
- fixed calibration buckets (for example deciles or fixed 10-point bands) with
  count, mean predicted probability, observed frequency, and confidence
  interval;
- ROC-AUC and PR-AUC as secondary discrimination metrics;
- excess-return mean, median, downside, and turnover for probability buckets,
  without presenting them as executable strategy returns.

Publish pooled metrics only with fold-level dispersion. If the latest fold is
materially miscalibrated, the live probability gate closes even if pooled Brier
Score looks acceptable.

## 8. Deterministic research states

Buy and sell are asymmetric. The state engine consumes long-term thesis status,
valuation, calibrated timing, position context, event invalidation evidence,
and data quality. It emits research states only.

| Position | Long-term thesis | Timing | State |
|---|---|---|---|
| none | supported, valuation acceptable | favorable and valid | `ENTRY_RESEARCH_CANDIDATE` |
| none | supported | weak/uncertain | `WATCH` |
| held | supported | favorable/neutral | `HOLD` or `ADD_RESEARCH_CANDIDATE` |
| held | supported | weak | `NO_ADD` (not an automatic sell) |
| held | challenged, not broken | any | `REDUCE_REVIEW` |
| held | credible break evidence pending confirmation | any | `SELL_REVIEW` |
| held | deterministic invalidation condition satisfied by grounded evidence | any | `THESIS_INVALIDATED` |
| any | PIT/calibration/evidence gate failed | any | `INSUFFICIENT_EVIDENCE` |

Probability thresholds are policy configuration, not model facts, and are not
approved in Phase 1. `THESIS_INVALIDATED` cannot be produced merely by price,
volume, an LLM opinion, or a low timing probability. No state maps directly to
a broker order.

## 9. PIT and backtest risk register

### 9.1 Mandatory dataset gates

| Risk | Current status | Required gate |
|---|---|---|
| Survivorship bias | Current scanners/universes can use present membership | Use historical membership with effective dates, or restrict claims to a fixed explicitly biased cohort and label results non-generalizable |
| Delisting bias | yfinance-style convenience history is not proven complete | Verify delisted securities and terminal returns/corporate actions; otherwise no public historical win-rate claim |
| Fundamental look-ahead/revisions | Repository audit says not PIT safe | Join each fact by public filing/availability timestamp and preserve original/revised vintages; otherwise exclude from historical model |
| Historical constituents | No versioned PIT constituent store found | Store index/sector membership intervals and source snapshot; never backfill today's membership |
| News availability | Date filtering exists, historical corpus does not | Use archived records with published/ingested timestamps, deduplication and immutable payload hash; otherwise events are live-only |
| LLM extractor drift | No versioned historical event feature store | Freeze prompt/schema/model/version and persist validated extraction; re-extraction is a new dataset version |
| Price adjustment | Policy not explicit | One vendor/methodology for asset and benchmark, corporate-action tests, immutable snapshot |
| Benchmark/sector mapping | Not implemented | Versioned benchmark and sector membership effective at sample date |
| Overlapping labels | Not handled | Interval purge and horizon embargo in every fold |
| Missingness/provider failure | Existing modules track failures, model policy absent | Missing indicators plus fail-closed rules; never map failure to neutral sentiment or zero return |

### 9.2 Publication status

There are three allowed dataset statuses:

- `BLOCKED_PIT`: a mandatory availability or universe gate fails; no probability.
- `RESEARCH_ONLY_BIASED_DATASET`: useful for pipeline tests, but reports must
  name the bias and must not say calibrated historical win rate.
- `PIT_VALIDATED`: all mandatory gates have evidence and tests; calibrated
  probability may be displayed with its validation window and limitations.

At the time of this audit, the combined fundamentals/news/universe dataset is
`BLOCKED_PIT`. A price-only prototype may become
`RESEARCH_ONLY_BIASED_DATASET` after adjustment/delisting checks. It is not yet
`PIT_VALIDATED`.

## 10. Monitoring design (after offline validation)

Persist each live signal snapshot and compare only compatible model, feature,
benchmark, and calibration versions. A material-change alert may fire when a
configured absolute probability delta is crossed (illustrative example:
`0.54 -> 0.68`), a research state changes, or a grounded thesis event appears.

The alert explains deterministic deltas:

- changed feature values and direction;
- new/expired grounded event IDs;
- market/sector regime change;
- model or calibration version change (which invalidates direct comparison);
- data-quality changes.

Use model-appropriate attribution (standardized logistic contributions;
permutation or supported tree attribution) and describe it as model attribution,
not causality. Monitoring remains quote/research only and imports no trade
context.

## 11. Minimum implementation plan

### Slice A — contracts and fail-closed gates

- Add package skeleton, immutable schemas, dataset status, source availability,
  feature snapshot, label, fold, calibrated output, and research-state enums.
- Add an import-boundary test forbidding official broker modules.
- Add PIT gate tests for future timestamps, unknown availability, wrong ticker,
  missing benchmark, and incomplete horizon.

Acceptance: synthetic fixtures only; no model and no user-facing probability.

Implementation status (2026-11-10): complete. Added immutable contracts,
publication/model statuses, PIT availability gates, relative-label and
walk-forward boundary types, research states, a validated-output guard, and an
AST import-boundary test. Offline tests use synthetic fixtures only.

### Slice B — price/volume and labels

- Implement deterministic features listed in section 6.
- Implement session-aligned 20D/60D benchmark-relative labels.
- Add corporate-action, missing-bar, duplicate-date, warm-up, and no-look-ahead
  tests using tiny hand-calculated fixtures.

Acceptance: feature values and labels reproduce expected fixture results;
forward rows are never visible to feature builders.

Implementation status (2026-11-10): core implemented. Added an explicit
adjusted-OHLCV contract, deterministic V1 price/volume indicators, strict
future/duplicate/mixed-adjustment rejection, warm-up missingness, and
shared-session 20D/60D relative-return labels. A production vendor adapter,
delisting evidence, and immutable corporate-action snapshot are still required
before Slice B or the dataset can be called PIT validated.

### Slice C — dataset and walk-forward baseline

- Add dataset fingerprints and purged expanding-window splitter.
- Add optional sklearn dependency and prevalence/Logistic/HistGradient baselines.
- Persist fold definitions and out-of-fold predictions.

Acceptance: tests prove ordering, purge/embargo, transform fitting only on train,
and untouched final holdout.

### Slice D — calibration and evaluation

- Add disjoint sigmoid/isotonic calibration.
- Add Brier, Log Loss, calibration buckets, fold/slice reports and baseline skill.
- Enforce a deployability gate and null probability on failure.

Acceptance: intentionally miscalibrated fixtures fail closed; reports distinguish
raw score from calibrated probability.

### Slice E — grounded event features

- Add the closed event taxonomy and validator over existing event evidence.
- Persist extractor and evidence lineage; aggregate deterministically.
- Run price-only versus price-plus-event ablations on a PIT-qualified corpus.

Acceptance: prompt injection, unsupported numeric values, future events,
duplicate syndication, missing publication time, and extractor drift are tested.

### Slice F — state engine and monitoring

- Combine the separately versioned long-term and timing outputs.
- Add asymmetric held/not-held states and thesis invalidation contract.
- Add compatible-version signal delta alerts and driver deltas.

Acceptance: low timing probability alone produces at most `NO_ADD`; never
`SELL_REVIEW` or `THESIS_INVALIDATED`. No broker call is reachable.

### Slice G — risk authorization and automatic-execution interface

- Define broker-neutral `OrderIntent`, `RiskSnapshot`,
  `ExecutionAuthorization`, `ExecutionReceipt`, and `ReconciliationResult`
  contracts, adapting the strongest existing portfolio/risk/broker contracts
  rather than duplicating them.
- Require explicit environment (`SIMULATE` initially), account allowlist,
  maximum gross/single-name/sector exposure, order notional and daily loss
  limits, liquidity/slippage bounds, quote freshness, session checks, model and
  data validity, idempotency key, authorization expiry, and kill switch.
- Keep signal generation incapable of constructing an authorization. Use a
  deterministic risk service and an independently configured execution service.
- Test with fake and simulation transports first, including timeout-after-submit,
  duplicate submission, partial fill, stale quote, changed account, restart,
  cancel failure, and reconciliation.
- Real-money activation, credentials, and broker writes require a separate
  future approval and operational readiness review; they are outside V1.

Acceptance: an authorized simulation request can traverse the interface and be
reconciled; every missing, stale, mismatched, duplicated, or over-limit input
fails closed. No test or default configuration can address a real account.

## 12. Phase 1 decisions requiring confirmation before implementation

1. Approve a new isolated `signal_engine` package rather than modifying current
   research and execution modules.
2. Approve `scikit-learn` as an optional dependency.
3. Choose the first research universe and obtain a defensible historical
   membership/delisting source; otherwise accept a clearly biased prototype.
4. Confirm SPY total return as the primary US-equity benchmark and declare
   sector ETFs/mapping source.
5. Choose or license a historical news corpus with publication and ingestion
   timestamps before event backtesting.
6. Decide whether the first implemented milestone should be a price-only,
   research-only baseline while PIT fundamentals/news remain excluded.
7. Confirm that automatic execution should first target the existing Moomoo
   `SIMULATE` sandbox through a broker-neutral interface; real trading remains a
   separately approved future capability.

## 13. Public GitHub positioning

The eventual public description should be:

> A grounded, quantitative multi-agent investment research system that separates
> long-term thesis from timing, converts sourced events into validated structured
> features, and reports walk-forward calibrated probabilities with explicit
> point-in-time limitations.

Position risk governance as the core: grounded evidence, calibrated uncertainty,
portfolio exposure controls, fail-closed authorization, and auditable
reconciliation. The automatic-execution interface is a controlled downstream
capability, not the headline promise. Do not market it as a guaranteed win-rate
engine or imply that real-money automation is enabled. Keep simulated execution
as the first isolated implementation target.

## 14. Local user risk console

The implemented local console is documented in `USER_APP_V1_DESIGN.md`. It
supports memory-only AI configuration, manual portfolios, and read-only real or
paper/simulated Moomoo/IBKR account snapshots. Users can enter a proposed buy or
sell point and receive deterministic before/after risk analysis. The console
does not make the user's decision and contains no broker-write capability.

