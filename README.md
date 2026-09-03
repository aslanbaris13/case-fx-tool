# fx-tool — currency conversion for an AI agent

One HTTP endpoint an agent can call as a tool. It converts an amount between two
currencies on a given date, using ECB rates from the public
[Frankfurter API](https://frankfurter.dev).

It is built around one rule: **never invent a rate, and never present a rate as
belonging to a date it does not belong to.**

## Setup

Python 3.10+.

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## Run

```bash
PORT=8080 ./run.sh
```

```bash
curl "http://localhost:8080/tools/convert?amount=250&from=EUR&to=TRY&date=2026-08-28"
```

## Test

```bash
./test.sh
```

The tests never touch the network — the upstream is faked with `respx` — so they
also pass with the upstream pointed at a closed port:

```bash
FX_UPSTREAM_BASE=http://127.0.0.1:1 ./test.sh
```

## Configuration

| Variable | Default | Meaning |
|---|---|---|
| `FX_UPSTREAM_BASE` | `https://api.frankfurter.dev` | Upstream base URL. The real host is not hardcoded anywhere. |
| `PORT` | `8080` | Port the service listens on. |

## The endpoint

```
GET /tools/convert?amount=250&from=EUR&to=TRY&date=2026-08-28
```

```json
{
  "amount": 250.0,
  "from": "EUR",
  "to": "TRY",
  "rate": 56.1718,
  "result": 14042.95,
  "rate_date": "2026-08-28",
  "asked_date": "2026-08-28",
  "is_fallback": false,
  "note": null,
  "source": "ECB via frankfurter.dev"
}
```

| Field | Meaning |
|---|---|
| `rate` | The rate as the upstream published it, at full precision (not rounded). |
| `result` | `amount × rate`, computed with `Decimal` and rounded once, `ROUND_HALF_UP`, to 2 places. |
| `asked_date` | The date the caller asked for. |
| `rate_date` | The date the rate **actually** belongs to, read from the upstream's own `date` field. |
| `is_fallback` | `true` when `rate_date != asked_date`. |
| `note` | When `is_fallback` is true, a sentence the model can pass to the customer. Otherwise `null`. |

### rate_date vs asked_date

The ECB publishes rates only on business days. Ask for a weekend or a holiday and
the upstream answers with the most recent earlier business day — correct
behaviour, but the caller has to know. So the two dates are always reported
separately, and when they differ it is flagged explicitly:

```
GET /tools/convert?amount=250&from=EUR&to=TRY&date=2026-08-30   # a Sunday
```

```json
{
  "rate_date": "2026-08-28",
  "asked_date": "2026-08-30",
  "is_fallback": true,
  "note": "No ECB rate was published for 2026-08-30; used the most recent rate, from 2026-08-28."
}
```

## Errors

Every failure returns a non-2xx status and the same shape:

```json
{ "error": "future_date", "message": "No rate exists yet for 2027-01-01; that date is in the future." }
```

| `error` | HTTP | Raised when |
|---|---|---|
| `invalid_request` | 400 | A required query parameter is missing. |
| `invalid_amount` | 400 | `amount` is missing, not a number, zero, negative, or has more than 2 decimals. |
| `invalid_date` | 400 | `date` is not a valid `YYYY-MM-DD` calendar date. |
| `future_date` | 400 | `date` is in the future, so no rate exists yet. |
| `before_series` | 400 | `date` predates the published ECB series. |
| `unknown_currency` | 400 | `from` or `to` is not a currency the upstream knows. |
| `upstream_timeout` | 504 | The upstream did not answer within 10s. |
| `upstream_unreachable` | 502 | The upstream could not be reached. |
| `upstream_error` | 502 | The upstream returned a 5xx. |
| `upstream_bad_response` | 502 | The upstream returned an unexpected status, or a body that is not the JSON we expect. |

## What it does in each case

| Case | Behaviour |
|---|---|
| No rate published (weekend, holiday) | **200.** Answers with the most recent earlier rate, reports its true `rate_date`, sets `is_fallback: true` and explains it in `note`. |
| Date in the future | **400 `future_date`**, decided locally before any upstream call. |
| Date before the series starts | **400 `before_series`**. |
| Unknown currency code | **400 `unknown_currency`**. |
| `from` equals `to` | **200** with `rate: 1.0` and `result` equal to `amount`; no upstream call. |
| Upstream is slow | **504 `upstream_timeout`** once the 10s timeout expires. |
| Upstream returns 500 | **502 `upstream_error`**. No rate is invented. |
| Upstream returns non-JSON | **502 `upstream_bad_response`**. |
| `amount` missing, 0, negative, or 10 decimals | **400 `invalid_amount`**. |

A 404 from the upstream is ambiguous — an unknown currency and a pre-series date
look identical. It is resolved by re-asking `/latest` with the same currencies:
if that succeeds the currencies are fine and the date is the problem, so the
series-start boundary is never hardcoded.

Repeating the same question does not re-ask the upstream: results are cached in
process by `(from, to, date)`. Today is deliberately not cached, because its rate
can still change when the ECB publishes.

## Scope

One endpoint, nothing else — no auth, database, UI, Dockerfile, CI or deployment.
Design decisions and trade-offs are in [NOTES.md](NOTES.md); the review of
`tool.py` is in [REVIEW.md](REVIEW.md).
