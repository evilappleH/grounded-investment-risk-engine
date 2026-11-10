# Local User Risk Console V1

Status: implemented local V1 with offline tests; real broker interoperability
requires the user's running OpenD/TWS/IB Gateway and account-specific validation.

## Product boundary

The user console connects to the user's own account for **read-only** cash,
position, and quote analysis. Connection immediately synchronizes the portfolio
and runs risk analysis; users do not re-enter broker holdings manually. The user
may enter a proposed buy or sell price, quantity, planned time, and review
horizon. The system compares current and projected risk, but it does not
choose the action, create an order, authorize execution, place/modify/cancel an
order, or expose a broker-write method.

This is different from disabling real-account support. Real-account portfolio
reads are a core capability; real-account writes are absent.

## Supported local connections

| Provider | Account environment | V1 access |
|---|---|---|
| Manual | User-entered portfolio | Local risk analysis |
| Moomoo OpenD | REAL | Read account, USD cash, US positions and market value |
| Moomoo OpenD | SIMULATE | Same read-only snapshot path |
| IBKR TWS/IB Gateway | Real ports 7496/4001 | Read-only handshake, account summary, positions and quotes |
| IBKR TWS/IB Gateway | Paper ports 7497/4002 | Same read-only snapshot path |

Every broker host must be loopback. The configured account ID must exactly
match an account discovered by the local gateway. Connections are opened for a
snapshot and then closed. Unsupported short, non-USD, non-US, missing-price, or
ambiguous-account data fails closed.

For IBKR, users should additionally enable the platform's Read-Only API option.
The application connector itself has no order methods.

## AI configuration

The UI supports the provider families already present in TradingAgents:
OpenAI, Anthropic, Google, Azure OpenAI, OpenRouter, DeepSeek, Qwen, Ollama, and
generic OpenAI-compatible endpoints. Keys are held in process memory, excluded
from responses and logs, and cleared with the local session/process. V1 risk
limits and exposure calculations remain deterministic; AI selection is for the
later grounded explanation/agent pipeline and cannot override a risk finding.

## Risk outputs

- net asset value, cash weight, and gross exposure;
- maximum single-position exposure;
- sector exposure and unknown-sector warnings;
- concentration HHI and position-count complexity;
- stale/future portfolio blocking;
- user-entered buy/sell-point projected cash, gross exposure, maximum position,
  and HHI deltas;
- planned-time conversion to US Eastern, a regular-session candidate check,
  and a mandatory re-sync flag when the current portfolio will be more than 15
  minutes old at the planned time;
- insufficient-cash and sell-more-than-held blocking.

Outputs distinguish facts, inferences, and recommendations. A buy/sell-point
review always carries `user_decision=REQUIRED`, `order_created=false`, and
`broker_write=false`.

The session check is deliberately incomplete for exchange holidays; the result
therefore carries `US_MARKET_HOLIDAY_CALENDAR_NOT_VALIDATED` until a versioned
exchange calendar is added. It must not claim that a planned timestamp is a
valid trading session solely from weekday and clock time.

## Local security

- loopback bind only;
- no hosted deployment because broker gateways are local and secrets are
  user-owned;
- memory-only session settings;
- no request-body or secret logging;
- same-origin browser writes and restrictive CSP;
- 64 KiB request limit;
- no persistence of account IDs, keys, or portfolio snapshots in V1;
- live trading implementation remains outside this user console.

## Run

```powershell
python -m investment_agents.user_app.server
```

Open `http://127.0.0.1:8765`.
