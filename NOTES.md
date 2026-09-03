# Notes

## Decisions

**Being honest about which day the rate is from.** `rate_date` is read from the
upstream's own `date` field and never copied from `asked_date`. Frankfurter
already walks back to the last published business day, so I did not reimplement
that: asking for Sunday 2026-08-30 returns 2026-08-28, and 2026-01-01 returns
2025-12-31 — it skips holidays and several days at a time. Writing my own
walk-back would have meant modelling the ECB holiday calendar locally, which is
fragile, and would have cost extra upstream calls for a job the upstream already
does correctly.

**Making the fallback visible.** When `asked_date != rate_date` the response sets
`is_fallback: true` and adds a `note` sentence. Returning the two dates alone
would have been the minimum, but the caller here is a language model talking to a
paying customer, and I did not want to depend on it noticing that two strings
differ. An explicit boolean is something it can branch on; the `note` is a
sentence it can pass on without composing its own.

**What the upstream cannot answer, I reject rather than dress up.** A future date
is refused before any upstream call, comparing against today in **UTC** — ECB
dates are UTC, and using local time would misjudge dates for a few hours around
midnight. For a pre-series date the upstream is no help on its own: an unknown
currency and a date before the series both come back as `404 {"message":"not
found"}`. Rather than hardcode the 1999-01-04 boundary, I re-ask `/latest` with
the same currencies — if that succeeds the currencies are valid and the date is
the problem (`before_series`), otherwise it is `unknown_currency`. It costs one
extra request on an error path and keeps the boundary out of the code.

**Money is `Decimal`, and float never gets near it.** The rate is parsed with
`json.loads(..., parse_float=Decimal)`, so the number goes straight from the JSON
text into a `Decimal` instead of becoming a lossy float first; `amount` arrives as
a string and becomes `Decimal(amount)` for the same reason. Only the final result
is rounded, once, with an explicit `ROUND_HALF_UP`. The rate itself is returned at
the upstream's precision — rounding the rate first multiplies the error by the
amount (0.85889 → 0.86 is 11.10 off on 10 000). `amount` must be positive with at
most two decimal places.

**Cache.** Keyed by `(from, to, date)` — the three things that determine a rate.
Dropping the date would let one day's rate be served for another, which is the
exact failure this service exists to prevent. Only past dates are cached, because
their rates are fixed forever; today is deliberately skipped, since the ECB
publishes around 16:00 CET and the answer can still change. Weekend and holiday
queries that resolve to the same business day are *not* merged onto a shared key:
that would need the local holiday calendar I just avoided, and it would not even
save the first call, because the true `rate_date` is unknown until the upstream is
asked. No TTL or size cap — past rates never go stale and the query space is
small, so eviction would be premature.

**Errors.** Every failure is `{error, message}` with a non-2xx status, including
the ones FastAPI would otherwise reject in its own 422 format — a missing query
parameter is reshaped into `400 invalid_request`. Errors are raised as a single
exception type from wherever they are noticed, including inside the upstream call,
and rendered in one place, so no path crashes with a bare 500 and no path invents
a number. Each `message` is written as a sentence the calling model could repeat
to a customer.

**Scope.** Exactly one endpoint: I removed the `/health` check I had scaffolded,
since the brief scores a small thing done carefully over a larger one. `date` is
required — there is no "latest" mode, because the tool contract in the brief
always carries a date. No `.env` or `python-dotenv`: there are no secrets, both
settings are read from the environment with defaults, and the reviewer passes them
on the command line. Worked on `main` with a linear history; on a team each step
would have been a pull request.

## With another day

- One retry with a short backoff on a transient upstream 5xx before giving up.
- Structured logging for upstream failures and cache hits — today a failure is
  only visible in the response.
- A short-TTL cache for today's rate instead of skipping it entirely.
- A contract test run against the real upstream on a schedule, to catch a change
  in its response shape that mocked tests cannot see.
- `before_series` is slightly generic for a currency that joined the series later
  than 1999; the message could name that pair's own start date.

## AI tools

Claude Code, throughout. I worked in small steps — one commit per idea, each one
explained before it was written and questioned afterwards, so I could defend every
line. I made the design calls myself (the fallback flag, the cache key, rejecting
future dates locally, dropping `/health`) and had the tool argue them back to me.
The commits are co-authored so that this is visible rather than hidden.

## One thing the AI got wrong

Asked what the service would do with a future date, it reasoned that the
upstream's "nearest earlier rate" behaviour would apply and we would return
`is_fallback: true`. I ran the request instead of believing it. Frankfurter
answers `404` for a future date, `raise_for_status()` raised, nothing caught it,
and the endpoint returned a bare `500 Internal Server Error` with a body that was
not even JSON — worse than predicted, and precisely the failure this brief cares
about. That is why future dates are now rejected locally, before the upstream is
ever called.

A smaller one, in the tests: it mocked the upstream to return `TRY` while the
request asked for `USD`. The test failed with a `KeyError` and I had to work out
whether the bug was in the test or the code. It was the test — but only running it
told me that, which is the argument against trusting generated tests on sight.
