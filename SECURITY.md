# Security policy

## Supported branch

Security fixes are applied to the latest `main` branch.

## Reporting a vulnerability

Please use GitHub's private vulnerability reporting feature when it is enabled. Do not include broker credentials, account identifiers, portfolio snapshots, API keys, or live order details in a public issue.

## Local secret handling

- Keep AI API keys and broker settings in local environment variables or the local application session.
- Never commit `.env`, broker logs, account exports, screenshots containing account data, or generated research reports.
- Bind the risk console to localhost. It is not designed to be exposed directly to the public internet.
- Prefer broker read-only API settings. Verify the gateway environment before using any integration.
- The user-facing connector must not gain place/modify/cancel order methods.

## Execution boundary

Live broker execution is outside the supported user workflow. The optional Moomoo sandbox must remain broker-confirmed `SIMULATE`, explicitly account-bound, manually authorized, and fail-closed. Any change that introduces live order capability is a security-sensitive architectural change and should be rejected unless separately designed, reviewed, and tested.
