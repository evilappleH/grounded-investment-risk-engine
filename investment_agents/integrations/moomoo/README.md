# Moomoo OpenD SIMULATE-only integration

This package is an isolated adapter for the official Moomoo OpenAPI Python SDK
(`moomoo-api`) and a locally authenticated OpenD gateway. Authentication stays
inside OpenD. The adapter has no username, password, trade-unlock, or REAL-order
configuration path.

Official contract references:

- [Install the official Python SDK](https://openapi.moomoo.com/moomoo-api-doc/en/quick/demo.html)
- [Discover trading accounts](https://openapi.moomoo.com/moomoo-api-doc/en/trade/get-acc-list.html)
- [Query account funds](https://openapi.moomoo.com/moomoo-api-doc/en/trade/get-funds.html)
- [Query positions](https://openapi.moomoo.com/moomoo-api-doc/en/trade/get-position-list.html)
- [Query orders](https://openapi.moomoo.com/moomoo-api-doc/en/trade/get-order-list.html)
- [Place orders](https://openapi.moomoo.com/moomoo-api-doc/en/trade/place-order.html)
- [Modify or cancel orders](https://openapi.moomoo.com/moomoo-api-doc/en/trade/modify-order.html)
- [Get market snapshots](https://openapi.moomoo.com/moomoo-api-doc/en/quote/get-market-snapshot.html)

## Non-negotiable environment boundary

The only order-capable progression is:

```text
READ_ONLY_PROBE -> SIMULATE_VERIFIED -> SIMULATE_EXECUTION_ARMED
```

There is no REAL state, fallback, override, or environment flag. OpenD must
return exactly one compatible US simulation account. Its `acc_id` must exactly
match both `expected_acc_id` and `simulate_account_allowlist`; its returned
`trd_env` must be `SIMULATE`. Account discovery may legitimately co-return REAL
and SIMULATE rows; REAL rows are ignored for selection and remain permanently
execution-ineligible. The exact expected account ID must identify one unique,
compatible US SIMULATE row and must be present in the explicit allowlist. A
REAL/unknown selected environment, wrong or missing account, duplicate selected
identity, or selected-account change immediately produces
`MOOMOO_REAL_EXECUTION_FORBIDDEN`. No account is selected by row order. Account
type is recorded but never used to authorize leverage.

Default connectivity is local `127.0.0.1:11111`. Initial connection and every
reconnect return to `READ_ONLY_PROBE`. Account identity and OpenD liveness are
rechecked immediately before each submission or cancellation.

## V1 product and order boundary

- US stocks and ETFs only, identified as `US.<ticker>`.
- USD cash-funded, long-only positions.
- Positive whole-share quantity only.
- `LIMIT` / `DAY` orders in the regular session only.
- `BUY` or `SELL_TO_REDUCE_EXISTING_LONG`; reconciled sellable quantity must
  cover every sell.

Options, futures, market orders, shorts, margin-dependent strategies, other
regions, other currencies, and unsupported instruments are rejected. Numeric
freshness, spread, and limit-deviation controls reuse
`EXPERIMENTAL_EXECUTION_POLICY` configuration; they are safety defaults, not
investment or optimality claims.

## Idempotency, recovery, and audit

The adapter reuses the verified research manifest, proposal hash, deterministic
Risk Gate result, kill switch, quote checks, and append-only SHA-256
hash-chained Paper execution journal. It writes `SUBMITTING` durably before the
SDK dispatch. Missing or ambiguous acknowledgement becomes
`UNKNOWN_BROKER_STATE`; no blind retry is permitted.

Restart recovery begins in read-only mode, repeats exact SIMULATE account
verification, and reconciles current orders, historical orders, deals, cash,
and positions before any further submission. Audit events link the source
manifest and methodology version, decision/proposal/risk hashes, session,
quote, request, acknowledgement, state transitions, deals, and returned fees.

## Isolated broker-plumbing smoke purpose

`PAPER_BROKER_SMOKE_TEST` is a separate typed route for a human-authorized
connectivity smoke. It has no research, committee, proposal, Risk Gate, or
source-manifest fields and cannot be passed to the production execution API.
It is hard-limited to the explicitly configured Paper account, broker-returned `SIMULATE`,
`US.AAPL`, one whole share, BUY, LIMIT, DAY, one tick below the current bid,
and an automatic cancel workflow. Every account, session, RTH, quote,
available-cash, price, kill-switch, duplicate, acknowledgement,
reconciliation, and hash-chained-journal control remains active. Any fill stops
the workflow without another order.

For cash-only US equity checks, normalized availability is
`US cash - frozen cash`, additionally capped by USD cash buying power when
OpenD supplies it. Futures-only `available_funds`, withdrawable cash, and
generic buying power are not treated as cash available for this route.

### SIMULATE evidence capabilities

The explicit SIMULATE capability profile supports current-order queries,
historical-order queries, and positions. It does not support current deals,
historical deals, or deal push. Reconciliation therefore uses exact broker
order IDs, `qty`, `dealt_qty`, `dealt_avg_price`, status, price, and update time;
positions are a secondary consistency check. The unsupported deal endpoint is
never required or dispatched for SIMULATE.

The SDK is loaded lazily. Install it only in an environment that needs a local
OpenD probe or explicitly authorized simulation execution:

```text
pip install moomoo-api
```

No order is submitted by the integration test or probe utilities.
