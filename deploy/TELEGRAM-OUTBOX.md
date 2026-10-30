# Durable Telegram publication

Both automatic refresh and manual publication use the `telegram-outbox` Git
branch in the main repository. It is separate from the site/main branch.
Every successful build persists meaningful events before attempting deployment.
After public verification, each destination receives only its undelivered events,
including events from previous runs. Empty current changes do not drop backlog.

The branch is public: it stores only already-public deal descriptions and opaque
HMAC destination identifiers, not chat/topic IDs, bot tokens or message IDs.
Destination identities use the bot token as their HMAC key. Token rotation fails
closed until the delivery identities are deliberately migrated; it never silently
resets delivery status. No new credential is required. Existing repository-scoped
push credentials persist the ledger, and manual publication uses contents:write.

Before each external send, a `sending` claim is pushed using Git fast-forward
conflict protection. Confirmed sends become `delivered`; explicit rejections become
`failed` and are retried on the next successful verified run. Successful targets
are skipped, even when another target fails. Receipts remain in Actions artifacts.

Telegram has no idempotency key for sendMessage. Transport timeouts, HTTP 5xx,
runner cancellation, or failure to save a successful receipt leave `sending`.
These uncertain targets are paused and privately alerted rather than blindly
resent. An operator must inspect the destination and mark the affected event IDs
`delivered` if present, or `failed` if proven absent, through a reviewed ledger
commit. Do not delete/reset the ledger: missing ledger fails closed.

Recovery is processed on the next successful scheduled refresh or the existing
manual `telegram-publish.yml` workflow; Cloudflare-only manual retries do not
invoke GitHub notifications. Recovery diagnostics are private, never group spam.

Migration baseline: `deploy/telegram-baseline.json` contains hashes of the history
present at migration. This prevents replay of pre-migration history. Today's six
targets were confirmed delivered by manual run `40674562222`. Older history is
excluded as migration baseline, not claimed as newly verified deliveries. Future
events are retained in the ledger until delivered, independent of the site's
30-day history retention. Do not regenerate baseline during ordinary refreshes.

One-time creation uses `TELEGRAM_OUTBOX_BOOTSTRAP=true python -m
tools.telegram_outbox` from the trusted checkout. Production never enables this
flag. If ledger persistence fails, publication stops before further sends.

## Acceptance (2026-10-28)

- Manual run `40675860551`: all six destinations returned `already-delivered`;
  no duplicate publication was sent.
- Automatic run `40675809825`: 345 tests passed, queue persisted, public files
  verified and six destinations skipped.
- Scheduler run `40675846226`: tests, queue persistence, deployment, verification,
  notification and continuation all passed. Next daily run: `40676900965`.
- Simulated failures cover partial delivery, next-run recovery with empty new
  changes, ambiguous responses, state-write failure, concurrent identical claims,
  missing/changed credentials and migration baseline. No failure injection was
  performed against the real Telegram groups.

## Recovery (2026-10-30)

Run `41011490811` passed 353 tests, Pages upload and four-file live verification,
but stopped during durable publication state processing. The old error handler
discarded the underlying operation, so its exact Git failure cannot be recovered
from that log. The ledger confirmed one target delivered and five still pending,
with no uncertain claims. Manual recovery `41012071924` completed successfully;
all six target ledgers now mark all eleven events delivered, without resetting
the ledger or replaying the successful target.

State diagnostics now include only allowlisted Git operation/reason labels, never
raw command output. Remote reads get bounded retries. After a failed push response,
the controller accepts only observation of its exact intended remote commit;
it never blindly retries a write or overwrites a concurrent writer. Private alerts
distinguish ledger failure from bad credentials and report verified web success.
