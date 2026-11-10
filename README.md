# Grounded Investment Risk Engine

A risk-first, grounded multi-agent research system for portfolio analysis and trade-decision support.

The project is designed to answer **“what could go wrong, and does this proposed action fit the portfolio?”** before it tries to estimate return. It is not an autonomous trading bot and it does not make decisions for the user.

## What is implemented

- A local risk console that can read portfolio snapshots from Moomoo OpenD or IBKR Gateway/TWS.
- A Chinese/English interface with browser-language detection and a persistent manual switch.
- Read-only account discovery, position/cash synchronization, and automatic portfolio risk analysis.
- Proposed buy/sell review with price, planned time, horizon, and portfolio-impact checks.
- Concentration, cash, sector, currency, stale-data, and position-level risk diagnostics.
- SEC CompanyFacts fundamentals, normalized provenance, deterministic valuation, and Buffett/Munger research personas.
- Grounded claim catalogs, structured agent records, deterministic committee synthesis, portfolio policy, and a fail-closed risk gate.
- Signal Engine V1 contracts, 20/60-trading-day benchmark-relative labels, availability gates, and interpretable price/volume features.
- Optional Moomoo **SIMULATE-only** execution plumbing kept as a sandbox. It is not part of the product's primary workflow.

## Safety boundary

The user-facing broker connectors expose no place, modify, cancel, or authorization methods. They obtain a short-lived account snapshot and disconnect. The local API reports `order_capability: ABSENT`.

Real accounts may be connected for analysis, but the application does not submit live orders. A user enters a proposed buy or sell point and remains the decision-maker.

The separate Paper sandbox requires an explicitly configured account, a broker-confirmed `SIMULATE` environment, a human authorization, a kill switch, and additional fail-closed checks. Real execution is forbidden in that route.

## Signal probabilities: current status

This repository does **not** present an LLM confidence score as a win rate. A probability may be shown as a calibrated probability only after all of the following are available:

1. point-in-time-safe features and labels;
2. rolling or walk-forward validation;
3. probability calibration;
4. Brier score, log loss, and calibration-bucket reporting;
5. explicit dataset limitations for survivorship, historical constituents, fundamentals, and news.

Until those gates pass, the signal layer returns an unavailable or research-only state. The initial target is future 20D/60D excess return versus a declared benchmark, not absolute price direction. See [Signal Engine V1 design](investment_agents/SIGNAL_ENGINE_V1_DESIGN.md).

## Decision model

Long-term quality and valuation are kept separate from short- and medium-term timing:

| Long-term thesis | Timing/risk state | Research output |
| --- | --- | --- |
| Intact | Supportive | `HOLD` / eligible for user review |
| Intact | Weak or risk rising | `NO_ADD` / `REDUCE_REVIEW` |
| Uncertain | Any | `REVIEW_REQUIRED` |
| Broken by grounded evidence | Any | `THESIS_INVALIDATED` / `SELL_REVIEW` |

Sell states are not a mechanical inverse of buy states. Normal volatility alone is not treated as a broken thesis.

## Run the local risk console

Python 3.11+ is required. Create an isolated environment, then install:

```bash
python -m pip install -e ".[dev,signal,user-app]"
python -m investment_agents.user_app.server --open-browser
```

This opens `http://127.0.0.1:8765`. Keep the server bound to localhost. Start Moomoo OpenD or IBKR Gateway/TWS separately if you want account synchronization. The broker must be configured for local API access; use a read-only API setting where the broker supports it. Opening `investment_agents/user_app/static/index.html` directly is only a styled bilingual preview; it cannot connect to the local account API.

On Windows, the launcher also accepts an explicit interpreter:

```powershell
.\scripts\run_local_risk_ui.ps1 -PythonPath "C:\path\to\python.exe"
```

No API key or broker credential is committed to the repository. AI-provider credentials remain local to the user's session/environment.

## Test

```bash
python -m pytest investment_agents/tests -q
node --check investment_agents/user_app/static/app.js
```

Network-dependent integration tests are not required for the local unit suite. Broker connectivity requires the corresponding local gateway.

## Repository map

- `investment_agents/user_app/` — local portfolio risk console and read-only broker connectors
- `investment_agents/risk/` — fail-closed risk controls
- `investment_agents/signal_engine/` — labels, feature contracts, and probability availability gates
- `investment_agents/committee/` — grounded claims and deterministic synthesis
- `investment_agents/valuation/` — deterministic valuation
- `investment_agents/integrations/` — broker integrations; execution code is isolated from the user console
- `tradingagents/` — upstream TradingAgents research framework

## Data and investment disclaimer

This is research software, not investment advice. Data may be delayed, incomplete, revised, or incorrectly mapped. Point-in-time limitations can invalidate backtest conclusions. Validate broker state independently before acting. The software provides decision support, not a recommendation, guarantee, or fiduciary service.

## Attribution and license

This work is based on [TauricResearch/TradingAgents](https://github.com/TauricResearch/TradingAgents) and retains its Apache-2.0 license. Adapted Buffett/Munger persona material from `virattt/ai-hedge-fund` is documented in [UPSTREAM_ATTRIBUTION.md](investment_agents/agents/UPSTREAM_ATTRIBUTION.md) under its MIT terms.

See [ATTRIBUTION.md](ATTRIBUTION.md), [SECURITY.md](SECURITY.md), and [LICENSE](LICENSE).
