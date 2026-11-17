# October 4 check-in rollback

The regular production job runs `checkin_october.py`, using the historical
engine at `852253d9befa060d2d14a941da110fa45fc22706` (October 4, 2026).
`legacy/checkin_20261004.py` is an unchanged copy of that commit's `checkin.py`.
The wrapper makes the exceptions below explicit; it does not change Secrets.

## Behavior restored

- October's main loop, per-provider account order, result counting, and balance
  report layout. Every run processes all configured accounts, as October did.
- October's browser/WAF preparation and check-in sequence for AnyRouter. A
  direct login-read-only result no longer replaces its normal check-in flow.
- October's Bearer/TokenRouter flow for GuysCode and its Xiaobai flow, including
  encrypted token-state loading and checkpointing before token rotation.
- October's HTTP flow for the other original providers, subject to the explicit
  compatibility exceptions below.

## Compatibility retained

- All current production Secret mappings, account names/aliases and deduplication,
  active-provider settings, current domains and PATs. New sites are dispatched
  through the current adapters. No account is limited to October's old slot range.
- Kapibala's current browser login and Twinkle's current JSON/verification API.
- GoRouter/Turnstile adapters, including 91Token's required token header and
  bounded GET confirmation after at most one submission. October's challenge
  attempts and eight-second reload delay are already the same; replaying an
  uncertain POST is not necessary for restoring those settings.
- SheApi's stop on invalid PAT and current CAPTCHA handling.
- Runtime ciphertext in `xiaobai/*.fernet` on `main`, rather than restoring
  October's obsolete `checkin-token-state` branch. Existing encrypted files and
  `XIAOBAI_TOKEN_STATE_KEY` must be retained together.
- Strict balance parsing and the wording “本轮余额变化”: a balance delta is not
  proof of a new award. Missing balances cannot overwrite known amounts with $0.
- Recognition of the newer `checked_in: true` status field, so a confirmed
  daily check-in still prevents another submission through the October engine.
- Multipart notifications and current cache expression repairs. Manual targeted
  runs do not send external notifications or overwrite the full-run balance hash.
- The existing external scheduler, runner, proxy pool and dependency lockfile.
  Do not reintroduce October's native cron alongside the external scheduler.

Diagnostics continue to use `checkin.py`; regular production uses the historical
entrypoint. `CHECKIN_DAILY_STATE_FILE` stays disabled as it was in October.
An account confirmed earlier today may still fail a later round's authentication;
the report describes that round. This rollback does not establish that a site's
403 or Turnstile challenge has been fixed.

## Backup and recovery

Before activation, both current Secret scopes and encrypted runtime tokens were
backed up and decrypt-verified. The one-time workflow was approved and completed
in Actions run `44065881858` on November 17, 2026, and was removed after verification.
It encrypted the snapshots in memory using AES-256-GCM, with the data key sealed
using RSA-OAEP/SHA-256. Only ciphertext was uploaded as a short-lived artifact.
The downloaded snapshots, verification reports, repository bundle and private
key are stored outside the repository in the local audit folder. The private
key is protected by the local Windows user via DPAPI; that key file alone is not
portable to another login. Never commit the private key, decrypted payload, or
Secret values.

Verification resolved all 88 active production accounts across 21 providers and
decoded all 46 runtime token files. Six GuysCode files already had a pending
refresh marker; these were preserved. A script rollback cannot recover tokens
whose server-side refresh outcome was interrupted.

The pre-change deployment was `5e524e60f26cf68eb120e855ab1024989797e8e8`.
To restore its runner behavior, change the regular workflow command from
`uv run checkin_october.py` to `uv run checkin.py`. Keep newer runtime ciphertext
and all current Secrets; do not reset the entire repository to an old revision.
