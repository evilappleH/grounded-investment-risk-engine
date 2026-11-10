# IBKR Paper-only integration

This package implements a fail-closed adapter for the official IBKR TWS socket
API. The official `ibapi` dependency is loaded lazily and must be installed from
an IBKR-supported TWS API distribution. It is not vendored and is not replaced
with an unofficial trading library.

Official contract references:

- [IBKR TWS API documentation](https://ibkrcampus.com/campus/ibkr-api-page/twsapi-doc/)
- [IBKR TWS API object reference](https://ibkrcampus.com/campus/ibkr-api-page/twsapi-ref/)
- [IBKR connection lifecycle](https://interactivebrokers.github.io/tws-api/connection.html)
- [IBKR account summary callbacks](https://interactivebrokers.github.io/tws-api/account_summary.html)
- [IBKR open-order behavior](https://interactivebrokers.github.io/tws-api/open_orders.html)

## Non-negotiable boundary

The only session progression is:

```text
READ_ONLY_PROBE -> PAPER_VERIFIED -> PAPER_EXECUTION_ARMED
```

There is no live state, live fallback, live override, or configuration flag that
can establish account safety. The broker must report exactly one account; that
exact value must equal `expected_account_id`, appear in
`paper_account_allowlist`, and not appear in `live_account_denylist`. Missing,
changed, multiple, or unexpected identities produce
`LIVE_OR_UNKNOWN_ACCOUNT_FORBIDDEN` and disarm the session.

Initial connections always request `READ_ONLY_PROBE`. IBKR's TWS-side “Read Only
API” setting is recommended as an extra safeguard but is not used as Paper
identity evidence. Reconnect also returns only to `READ_ONLY_PROBE`.

## Supported V1 execution surface

- `STK` contracts only, including supported ETFs represented by IBKR as `STK`.
- USD cash-funded orders only.
- `LMT` orders only; default time in force is restricted to configured values.
- `BUY` or `SELL_TO_REDUCE_EXISTING_LONG`; a sell must be covered by the
  reconciled long position.
- Fractional quantities only when both local configuration and resolved contract
  capability explicitly allow them.

Options, futures, futures options, CFDs, warrants, combos, bonds, leveraged FX,
shorts, market orders, and margin-dependent submissions are unsupported.

All numerical execution limits are configuration values labeled
`EXPERIMENTAL_EXECUTION_POLICY`. Defaults exist for deterministic tests and are
not investment recommendations or claims of optimality.

## Durability and recovery

Every intent transition is written to an fsync'd, append-only, SHA-256
hash-chained JSONL journal. `SUBMITTING` is durable before `placeOrder`. An
ambiguous acknowledgement becomes `UNKNOWN_BROKER_STATE`; the adapter never
blindly retries and blocks new submissions until broker orders and executions
prove a resolvable state.

On restart, the adapter reconnects read-only, explicitly re-verifies the Paper
account, and reconciles account summary, positions, open orders, completed
orders, and executions before any further action. Existing order control can be
re-armed only for the same verified manifest, proposal, risk decision, and
intent. Cancellation is limited to a broker order already bound in the journal.

The application never asks for or stores an IBKR username, password, or 2FA
secret. Authentication remains entirely inside an already-running TWS or IB
Gateway session.
