# Event reliability and mock delivery

## Scope

The mock webhook stores authenticated `/hoi` events. PostgreSQL acts as a bounded
work queue. Independent event and delivery workers claim rows with short
transactions, release database connections before model or adapter calls, and
use changing lease tokens to fence stale workers. A grounded answer or an open
teacher ticket and its outbound notification are persisted atomically.

The delivery adapter writes to a local mock inbox. `SENT` means that this mock
adapter recorded a receipt; it does not mean a learner read the message. No real
Zalo credentials or network requests are used. Delivery is at least once at the
adapter boundary; a deterministic message key and unique receipt make retries
idempotent in this mock implementation.

## Event state

```text
PENDING / due FAILED_RETRYABLE / expired PROCESSING
    -> PROCESSING (new token, lease, attempt)
    -> ANSWERED or HANDOFF (outcome + outbox in one transaction)
    -> FAILED_RETRYABLE (sanitized code + bounded next_attempt_at)
    -> FAILED_FINAL (permanent failure or exhausted attempts)
```

Every finalization checks `status`, current `lease_token`, and lease expiry while
holding the event row lock. Expired work is reclaimable with a new token. A
worker whose token was replaced cannot persist an outcome, usage or outbox row.
Retries use bounded exponential backoff with injected jitter; retryable events
do not create tickets or outbound messages.

## Outbox and delivery state

The processor derives a deterministic key from the trusted inbound channel and
external event ID. It stores one `ANSWER_ACK` or `HANDOFF_ACK` from the backend
in the same transaction as the Answer/Ticket and terminal event transition.
The delivery worker claims an outbox row independently and validates the
current group/class binding, bot setting, and answer source versions before
dispatch. A failed safety check suppresses delivery. A handoff acknowledgment
only says that the request was received; the ticket remains `OPEN`.

```text
PENDING / due RETRY_WAIT / expired SENDING
    -> SENDING (new token and bounded attempt)
    -> SENT | RETRY_WAIT | DEAD | SUPPRESSED
```

The mock adapter inserts the receipt by message key with a unique constraint.
If it recorded a receipt but the dispatcher lost its acknowledgment, a retry
returns the same receipt. Network exactly-once delivery is not claimed.

## Admission and authorization

Webhook authentication happens before database access. Group binding and bot
state are resolved by the server; clients cannot select `class_id`. The event's
unique `(channel, external_event_id)` is inserted before quota reservation in a
single transaction, so duplicate delivery does not consume quota. Per-sender
and per-group fixed windows use atomic PostgreSQL upserts. A rejected quota
request rolls back both event and counters and returns `429` with `Retry-After`.

Sender identifiers are SHA-256 hashed before writing limiter keys. Operator
inspection uses a separate token and returns aggregate/sanitized data only.
Logs omit prompts, message bodies, sender identifiers, credentials and source
passages.

## Configuration

See `.env.example` for bounded worker lease/retry settings, separate webhook
and operator tokens, rate limits and window size. Defaults are for local demo
use and are not production capacity claims.

## Operations and limits

Run the event worker and delivery worker as separate processes. PostgreSQL is
the only queue dependency. A process crash during a provider request can cause
that provider call to be repeated after lease expiry; usage/cost before a lost
finalization cannot be attributed reliably. The mock receipt is durable and
idempotent by message key. No automatic operator ticket resolution, real Zalo
delivery, conversation memory or free semantic paraphrasing is included.
