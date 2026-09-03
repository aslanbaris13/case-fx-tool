# Review of tool.py

I ran `tool.py` against the live upstream rather than reading it alone, so every
number below is measured. The command that produces each one is under the
finding. Ranked by what it costs a paying customer.

## 1. The cache ignores the date, so one stale rate answers every request

`key = f"{base}-{target}"` has no date in it, and nothing ever expires. The first
lookup a process performs for a pair is returned for every later request for that
pair, whatever date is asked for.

**What it does to a customer.** I asked for 250 EUR → TRY on 2020-01-02, then for
250 EUR → TRY on 2026-08-28. The second answer was **₺1,667.50**. The correct
answer is 250 × 56.1718 = **₺14,042.95**. The customer is quoted 88% below the
real value — about ₺12,375 missing — with a confident `200` and a `rate_date` of
2026-08-28 to make it look right. In a service that stays up for hours, every
answer after the first is a number from whenever the process happened to start.

**Verify.** Restart the process, then:
```bash
curl "localhost:8000/tools/convert?amount=250&to=TRY&on=2020-01-02"   # rate 6.67
curl "localhost:8000/tools/convert?amount=250&to=TRY&on=2026-08-28"   # rate 6.67 again
curl "https://api.frankfurter.dev/v1/2026-08-28?base=EUR&symbols=TRY" # 56.1718
```

## 2. The documented `from` and `date` parameters do not exist

The handler declares `from_` and `on`. The brief's contract is `from` and `date`.
FastAPI binds by name, so a caller using the documented URL has both silently
ignored: `from_` falls back to its `"EUR"` default and `on` stays `None`, which
sends the request to `/latest`.

**What it does to a customer.** I asked for 250 USD → GBP on 2026-08-28. The
correct answer is 250 × 0.73624 = **£184.06**. The tool returned **£215.00** — it
had quietly converted EUR → GBP at today's rate — and the response's own `"from"`
field said `EUR`, contradicting the request. Nothing errors; a wrong currency pair
on a wrong day is returned as a success, 17% out.

**Verify.**
```bash
curl "localhost:8000/tools/convert?amount=250&from=USD&to=GBP&date=2026-08-28"
# -> "from":"EUR", rate 0.86, rate_date = today. Neither parameter took effect.
```

## 3. Every failure is answered with `rate: 0.0` and HTTP 200

`except Exception` swallows everything, prints one line to stdout, and returns a
body with `rate: 0.0, result: 0.0` — at status **200**.

**What it does to a customer.** A typo'd currency is enough. `to=ZZZ` returns
`{"rate":0.0,"result":0.0}` with `200 OK`; the only trace is `conversion failed:
'rates'` on stdout. The calling model has no way to distinguish this from a real
answer, so it tells the customer their 250 EUR is worth **0.00**. The same
response is produced by an upstream outage, a timeout, or a malformed body. A
service that reports success when it has failed cannot be monitored either — no
non-2xx rate to alert on.

**Verify.**
```bash
curl -i "localhost:8000/tools/convert?amount=250&to=ZZZ&on=2026-08-28"
# -> HTTP/1.1 200 OK, {"rate":0.0,"result":0.0,...}
```

## 4. `rate_date` is invented, never read from the upstream

`fetch_rate` returns `str(on or date.today())` as "the date the rate belongs to".
The upstream's own `date` field is never read, on any path.

**What it does to a customer.** I asked for Saturday 2026-08-29. The upstream
answered with Friday's rate and said so — `"date":"2026-08-28"`. The tool reported
`rate_date: "2026-08-29"`, attributing the number to a day on which the ECB
published nothing, with no flag to say a substitution happened. A customer
reconciling an invoice or disputing a charge is handed a rate for a day that has
no rate. The same defect shows on the `latest` path: the upstream said
`"date":"2026-09-02"` and the tool reported `2026-09-03`, because it uses the
server's local `date.today()` instead of the answer it was given.

The weekend branch does not help, and its comment is misleading: for a weekend the
upstream already returns a rate, so `target not in payload["rates"]` is false and
the branch never runs. Where it does run — an unknown currency, or an error body
with no `rates` key — it refetches `/latest`, which for an old date would
substitute *today's* rate for one years ago.

**Verify.**
```bash
curl "localhost:8000/tools/convert?amount=250&to=USD&on=2026-08-29"  # rate_date 2026-08-29
curl "https://api.frankfurter.dev/v1/2026-08-29?base=EUR&symbols=USD" # "date":"2026-08-28"
```

## 5. Money is `float`, and the rate is rounded to 2 dp before it is multiplied

`round(rate, 2)` then `round(amount * rate, 2)`, all in binary floating point.
Rounding the *rate* first is the expensive half: the error is then multiplied by
the amount.

**What it does to a customer.** The EUR → USD rate 1.1643 became 1.16, so 250 EUR
came back as $290.00 instead of $291.08 (250 × 1.1643 = 291.075). That is about
$1.08 on a small conversion and $4,300 on a million — a systematic loss on every
trade, always in the same direction for a given rate. Prices should be computed in `Decimal` from strings,
with a single rounding at the end and a stated rounding mode.

**Verify.** Compare `rate` in any response against the upstream's — 1.1643 is
reported as 1.16 — and multiply both by a large amount.

## Also

`UPSTREAM = "https://api.frankfurter.dev/v1"` is hardcoded, which the brief rules
out. Beyond the requirement, it is the reason findings 1–4 could survive: with no
way to point the service at a controlled upstream, none of these paths can be
exercised in a test, so nothing catches them before a customer does. The response
also omits `asked_date`, so a caller cannot compare the two dates even if
`rate_date` were correct.

## The one I would fix before shipping tonight

**Finding 1, the cache.** Findings 2–4 hand the customer the wrong *label* or the
wrong *default*; the cache hands them a wrong *price*, by a factor of eight, on
essentially every request after the first, while looking entirely healthy. The
safest change tonight is to delete the cache outright — three lines — and pay for
the extra upstream calls until it can be keyed by `(from, to, date)` with
un-dated `latest` lookups left uncached. Correctness first, then the rate limit.

I considered finding 2 for this slot, since a service that ignores its documented
parameters is arguably not the service at all. I chose the cache because its
answer is a plausible number that no one will question, whereas the wrong-default
bug at least returns a real, current rate for the pair it substitutes.

## Things that look suspicious but are fine

- **No timeout is configured on the HTTP client.** This is the first thing I
  looked for, and it is not a defect: `httpx.AsyncClient()` defaults to
  `Timeout(timeout=5.0)`, so the service will not hang on a slow upstream. I
  would still set it explicitly — a default that matters this much should be
  visible in the code — but nothing is broken today.
- **A module-level `AsyncClient` created at import and never closed.** Sharing one
  client is the recommended pattern; it reuses connections instead of building a
  new pool per request. Not closing it on shutdown is untidy in a process that
  runs until it is killed, not a customer-facing problem.
- **`payload.get("rates", {})` looks like a swallowed error.** It is defensive in
  the right way — the failure it leads to is caused by finding 3, not by this line.
