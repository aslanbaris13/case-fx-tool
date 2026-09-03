"""Offline tests for /tools/convert.

Every upstream call is faked with respx, so the whole suite passes with no
network at all (test.sh runs it with FX_UPSTREAM_BASE at a closed port). Routes
are registered against fx.upstream_base() so they match whatever base is set.
"""

from datetime import datetime, timedelta, timezone

import httpx
import respx

from app import fx

CONVERT = "/tools/convert"


def dated(date: str) -> str:
    return f"{fx.upstream_base()}/v1/{date}"


def latest() -> str:
    return f"{fx.upstream_base()}/v1/latest"


def ok(date: str, to: str = "TRY", rate: float = 56.1718) -> httpx.Response:
    """A well-formed Frankfurter response; `date` is the real rate date."""
    return httpx.Response(200, json={"amount": 1.0, "base": "EUR", "date": date, "rates": {to: rate}})


def today_iso() -> str:
    return datetime.now(timezone.utc).date().isoformat()


# ---------------------------------------------------------------- happy path

@respx.mock
def test_business_day_rate_date_equals_asked(client):
    respx.get(dated("2020-01-02")).mock(return_value=ok("2020-01-02"))
    r = client.get(CONVERT, params={"amount": "250", "from": "EUR", "to": "TRY", "date": "2020-01-02"})
    body = r.json()
    assert r.status_code == 200
    assert body["rate"] == 56.1718            # rate NOT rounded to 2dp
    assert body["result"] == 14042.95         # 250 * 56.1718, Decimal, rounded once
    assert body["rate_date"] == "2020-01-02"
    assert body["asked_date"] == "2020-01-02"
    assert body["is_fallback"] is False
    assert body["note"] is None


@respx.mock
def test_weekend_fallback_is_visible(client):
    # Asked Sunday, upstream answers with Friday's date -> must be flagged.
    respx.get(dated("2020-01-05")).mock(return_value=ok("2020-01-03"))
    r = client.get(CONVERT, params={"amount": "250", "from": "EUR", "to": "TRY", "date": "2020-01-05"})
    body = r.json()
    assert r.status_code == 200
    assert body["asked_date"] == "2020-01-05"
    assert body["rate_date"] == "2020-01-03"
    assert body["is_fallback"] is True
    assert "2020-01-03" in body["note"]


@respx.mock
def test_decimal_money_is_exact(client):
    # 0.1 + 0.2 territory: a rate/amount that a float would smear.
    respx.get(dated("2020-01-02")).mock(return_value=ok("2020-01-02", to="USD", rate=1.1))
    r = client.get(CONVERT, params={"amount": "0.3", "from": "EUR", "to": "USD", "date": "2020-01-02"})
    assert r.json()["result"] == 0.33        # 0.3 * 1.1 = 0.33 exactly, not 0.33000000004


# ------------------------------------------------------------ local validation

def test_amount_zero_rejected(client):
    r = client.get(CONVERT, params={"amount": "0", "from": "EUR", "to": "TRY", "date": "2020-01-02"})
    assert r.status_code == 400
    assert r.json()["error"] == "invalid_amount"


def test_amount_negative_rejected(client):
    r = client.get(CONVERT, params={"amount": "-5", "from": "EUR", "to": "TRY", "date": "2020-01-02"})
    assert r.status_code == 400
    assert r.json()["error"] == "invalid_amount"


def test_amount_too_many_decimals_rejected(client):
    r = client.get(CONVERT, params={"amount": "1.234", "from": "EUR", "to": "TRY", "date": "2020-01-02"})
    assert r.status_code == 400
    assert r.json()["error"] == "invalid_amount"


def test_amount_not_a_number_rejected(client):
    r = client.get(CONVERT, params={"amount": "abc", "from": "EUR", "to": "TRY", "date": "2020-01-02"})
    assert r.status_code == 400
    assert r.json()["error"] == "invalid_amount"


def test_bad_date_rejected(client):
    r = client.get(CONVERT, params={"amount": "250", "from": "EUR", "to": "TRY", "date": "banana"})
    assert r.status_code == 400
    assert r.json()["error"] == "invalid_date"


def test_missing_required_param_uses_our_error_shape(client):
    # A missing required param must not leak FastAPI's default 422 body.
    r = client.get(CONVERT, params={"amount": "250", "from": "EUR", "date": "2020-01-02"})
    assert r.status_code == 400
    assert r.json()["error"] == "invalid_request"
    assert "to" in r.json()["message"]


@respx.mock
def test_future_date_rejected_without_upstream(client):
    # No route registered: if the code called the upstream, respx would raise.
    future = (datetime.now(timezone.utc).date() + timedelta(days=10)).isoformat()
    r = client.get(CONVERT, params={"amount": "250", "from": "EUR", "to": "TRY", "date": future})
    assert r.status_code == 400
    assert r.json()["error"] == "future_date"


@respx.mock
def test_malformed_currency_rejected_without_upstream(client):
    # No route registered: a malformed code must be refused locally.
    r = client.get(CONVERT, params={"amount": "250", "from": "EUR", "to": "EUROS", "date": "2020-01-02"})
    assert r.status_code == 400
    assert r.json()["error"] == "unknown_currency"


@respx.mock
def test_same_currency_is_identity(client):
    respx.get(latest()).mock(return_value=ok(today_iso()))      # upstream knows EUR
    r = client.get(CONVERT, params={"amount": "250", "from": "EUR", "to": "EUR", "date": "2020-01-02"})
    body = r.json()
    assert r.status_code == 200
    assert body["rate"] == 1.0
    assert body["result"] == 250.0
    assert body["is_fallback"] is False


@respx.mock
def test_same_currency_unknown_code_is_not_passed_off_as_real(client):
    respx.get(latest()).mock(return_value=httpx.Response(404, json={"message": "not found"}))
    r = client.get(CONVERT, params={"amount": "250", "from": "ZZZ", "to": "ZZZ", "date": "2020-01-02"})
    assert r.status_code == 400
    assert r.json()["error"] == "unknown_currency"


@respx.mock
def test_same_currency_repeat_is_cached(client):
    route = respx.get(latest()).mock(return_value=ok(today_iso()))
    params = {"amount": "250", "from": "EUR", "to": "EUR", "date": "2020-01-02"}
    client.get(CONVERT, params=params)
    client.get(CONVERT, params=params)
    assert route.call_count == 1              # identity goes through the cache too


# ------------------------------------------------------------ upstream failures

@respx.mock
def test_unknown_currency(client):
    respx.get(dated("2020-01-02")).mock(return_value=httpx.Response(404, json={"message": "not found"}))
    respx.get(latest()).mock(return_value=httpx.Response(404, json={"message": "not found"}))
    r = client.get(CONVERT, params={"amount": "250", "from": "EUR", "to": "ZZZ", "date": "2020-01-02"})
    assert r.status_code == 400
    assert r.json()["error"] == "unknown_currency"


@respx.mock
def test_before_series(client):
    # dated 404 but /latest ok -> the date is the problem, not the currency.
    respx.get(dated("1998-06-01")).mock(return_value=httpx.Response(404, json={"message": "not found"}))
    respx.get(latest()).mock(return_value=ok(today_iso(), to="USD", rate=1.1))
    r = client.get(CONVERT, params={"amount": "250", "from": "EUR", "to": "USD", "date": "1998-06-01"})
    assert r.status_code == 400
    assert r.json()["error"] == "before_series"


@respx.mock
def test_upstream_500(client):
    respx.get(dated("2020-01-02")).mock(return_value=httpx.Response(500))
    r = client.get(CONVERT, params={"amount": "250", "from": "EUR", "to": "TRY", "date": "2020-01-02"})
    assert r.status_code == 502
    assert r.json()["error"] == "upstream_error"


@respx.mock
def test_upstream_timeout(client):
    respx.get(dated("2020-01-02")).mock(side_effect=httpx.TimeoutException("slow"))
    r = client.get(CONVERT, params={"amount": "250", "from": "EUR", "to": "TRY", "date": "2020-01-02"})
    assert r.status_code == 504
    assert r.json()["error"] == "upstream_timeout"


@respx.mock
def test_upstream_non_json(client):
    respx.get(dated("2020-01-02")).mock(return_value=httpx.Response(200, text="<html>nope</html>"))
    r = client.get(CONVERT, params={"amount": "250", "from": "EUR", "to": "TRY", "date": "2020-01-02"})
    assert r.status_code == 502
    assert r.json()["error"] == "upstream_bad_response"


# -------------------------------------------------------------------- cache

@respx.mock
def test_repeat_past_question_hits_upstream_once(client):
    route = respx.get(dated("2020-01-02")).mock(return_value=ok("2020-01-02"))
    params = {"amount": "250", "from": "EUR", "to": "TRY", "date": "2020-01-02"}
    client.get(CONVERT, params=params)
    client.get(CONVERT, params=params)
    assert route.call_count == 1              # second answer came from the cache


@respx.mock
def test_today_is_not_cached(client):
    today = today_iso()
    route = respx.get(dated(today)).mock(return_value=ok(today))
    params = {"amount": "250", "from": "EUR", "to": "TRY", "date": today}
    client.get(CONVERT, params=params)
    client.get(CONVERT, params=params)
    assert route.call_count == 2              # today can change, so re-asked
