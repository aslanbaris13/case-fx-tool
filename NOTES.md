# Notes

## Decisions

**Which day the rate is from.** `rate_date` comes from the upstream's own `date`
field, never copied from `asked_date`. Frankfurter already walks back to the last
published business day — Sunday 2026-08-30 returns 2026-08-28, and 2026-01-01
returns 2025-12-31, skipping holidays and several days at a time. Reimplementing
that would have meant modelling the ECB holiday calendar locally: fragile, and
extra calls for a job the upstream already does correctly.

**Making the fallback visible.** When `asked_date != rate_date` the response sets
`is_fallback: true` and adds a `note`. Returning both dates alone was the minimum,
but the caller is a language model talking to a paying customer, and I did not want
to depend on it noticing that two strings differ. The boolean is something it can
branch on; the note is a sentence it can pass on.

**What the upstream cannot answer, I reject.** A future date is refused before any
upstream call, against today in **UTC** — ECB dates are UTC, and local time would
misjudge dates around midnight. A pre-series date is harder: it and an unknown
currency both come back as `404 {"message":"not found"}`. Rather than hardcode the
1999-01-04 boundary, I re-ask `/latest` with the same currencies — if that works,
the currencies are fine and the date is the problem. One extra request on an error
path, and the boundary stays out of the code.

**Money.** The rate is parsed with `json.loads(..., parse_float=Decimal)`, so it
never passes through a float on the way in; `amount` arrives as a string and becomes
`Decimal(amount)`. Only the final result is rounded, once, `ROUND_HALF_UP`. The rate
is returned at the upstream's precision — rounding it first multiplies the error by
the amount (0.85889 → 0.86 is 11.10 out on 10,000). `amount` must be positive with
at most two decimals. The response casts to `float` purely to serialise: JSON has no
decimal type, the arithmetic and the rounding are already done, and a two-decimal
value round-trips through a double unchanged.

**Currency codes.** ISO 4217 codes are three ASCII letters, so malformed ones
(empty, `EUROS`, `12`, `€`) are refused locally — otherwise each costs two upstream
calls. Whether a well-formed code exists stays the upstream's judgement. A
same-currency conversion needs no rate lookup, but I still confirm the code is real:
without that check `from=ZZZ&to=ZZZ` returned `200`, `rate: 1.0` and `source: ECB`,
pricing a currency that does not exist. It shares the cache, so repeating it does
not re-probe.

**Cache.** Keyed by `(from, to, date)` — the three things that fix a rate. Drop the
date and one day's rate is served for another, the exact failure this service exists
to prevent. Only past dates are cached, since their rates never change; today is
skipped because the ECB publishes around 16:00 CET. Weekend queries that resolve to
the same business day are *not* merged onto a shared key: that needs the holiday
calendar I just avoided, and would not save the first call anyway, since the true
`rate_date` is unknown until the upstream is asked. No TTL or size cap — past rates
never go stale and the query space is small.

**Errors.** Every failure is `{error, message}` with a non-2xx status, including
those FastAPI would otherwise return in its own 422 format (a missing parameter
becomes `400 invalid_request`). One exception type is raised wherever a problem is
noticed, including inside the upstream call, and rendered in one place: no path
crashes with a bare 500, and no path invents a number. Each message is a sentence
the calling model can repeat to a customer.

**Scope.** One endpoint — I removed the `/health` check I had scaffolded. `date` is
required; there is no "latest" mode, since the brief's contract always carries a
date. No `.env`: there are no secrets, and both settings are read from the
environment with defaults. Linear history on `main`; on a team each step would have
been a pull request.

## With another day

- One retry with a short backoff on a transient upstream 5xx.
- Structured logging for upstream failures and cache hits.
- A short-TTL cache for today's rate rather than skipping it.
- A scheduled contract test against the real upstream, to catch a change in its
  response shape that mocked tests cannot see.
- `before_series` is generic for a currency that joined later than 1999; the
  message could name that pair's own start date.

## AI tools

Claude Code, used as a pair rather than a generator. It proposed implementations; I
chose between them, rejected some, and had it explain each line back to me before it
went in — one commit per idea, and it questioned me on every commit afterwards, so I
can defend the whole repo rather than recognise it.

Several of the fixes here started as problems I raised, not suggestions it made.
Working through the cache I pointed out that a Friday and a Saturday question
resolve to the same rate under different keys, which is why that trade-off is argued
explicitly above instead of left as an accident. Asking whether invalid currency
codes were genuinely covered turned up `from=ZZZ&to=ZZZ` answering `200` with
`rate: 1.0` for a currency that does not exist. Insisting on a clause-by-clause
audit against the brief turned up a missing query parameter escaping as FastAPI's
own 422 instead of our `{error, message}`, and settled the `/health` question.

The commits are co-authored so the tool's part is visible rather than hidden.

## One thing the AI got wrong

Asked what the service would do with a future date, it reasoned that the upstream's
"nearest earlier rate" behaviour would apply and we would return `is_fallback:
true`. I ran the request instead of believing it. Frankfurter answers `404`,
`raise_for_status()` raised, nothing caught it, and the endpoint returned a bare
`500 Internal Server Error` with a body that was not even JSON — worse than
predicted. That is why future dates are now rejected locally, before the upstream is
called at all.

A smaller one, in the tests: it mocked the upstream to return `TRY` while the
request asked for `USD`. The test failed with a `KeyError` and I had to work out
whether the bug was in the test or the code. It was the test — but only running it
told me that, which is the case against trusting generated tests on sight.
