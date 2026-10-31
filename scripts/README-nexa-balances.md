# NexaVlinks balance-only reads

Dispatch the existing workflow on this branch with only `nexa_balance_only=true`:

```sh
gh workflow run checkin.yml --ref feat/nexavlinks-readonly-balance -f nexa_balance_only=true
```

This selects only `nexa-balance-read-only`. All check-in, exchange, proxy and
diagnostic jobs are skipped, even when conflicting inputs are supplied.

The reader requires all 19 Nexa accounts from the existing production Secrets
(64, 65, 88-97, 112). It reads `/api/v1/auth/me` and preserves the returned balance
precision. If authentication expires, it reuses the existing encrypted,
compare-and-swap token refresh flow. It never reads check-in status, requests a
challenge, submits a check-in, sends notifications or calls an inference API.

Confirmed balances are exported to `nexa-live-balances-<run>-<attempt>` as
`nexa-balances.json`, including the observation time and GitHub run identity.
Failed accounts are omitted, never replaced by zero or stale values; any failure
makes the job fail. Tokens are not included in the balance artifact. Any pending
authentication recovery checkpoint is uploaded separately as ciphertext.

This branch does not change the production AxonHub breaker or schedule balance
polling. A GitHub dispatch is a point-in-time read, not a real-time hard spending
limit. Any consumer must validate freshness, run identity and missing accounts
before changing channels. Do not substitute the current balance for the day's
opening balance: that would reset the spending baseline.
