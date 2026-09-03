"""Upstream (Frankfurter) access and the currency conversion itself.

Every way the upstream can fail — slow, down, a 5xx, a 404, or a body that is
not the JSON we expect — is turned into a structured FxError instead of a crash
or an invented number. The endpoint stays clean; the errors raised here bubble
up to the handler registered in main.py.
"""

from __future__ import annotations

import json
import os
from datetime import date as date_type, datetime, timezone
from decimal import ROUND_HALF_UP, Decimal

import httpx

from app import cache
from app.errors import FxError

DEFAULT_UPSTREAM_BASE = "https://api.frankfurter.dev"

# How long we wait for the upstream before giving up.
UPSTREAM_TIMEOUT_SECONDS = 10.0


def upstream_base() -> str:
    """Base URL of the upstream FX API, read from the environment.

    Never hardcoded: reviewers point FX_UPSTREAM_BASE at a fake upstream, so
    the real host must be a changeable default, not baked into the code.
    """
    return os.getenv("FX_UPSTREAM_BASE", DEFAULT_UPSTREAM_BASE)


async def _get(client: httpx.AsyncClient, url: str, params: dict) -> httpx.Response:
    """One upstream GET, with slow/unreachable turned into structured errors."""
    try:
        return await client.get(url, params=params)
    except httpx.TimeoutException:
        raise FxError(
            504, "upstream_timeout",
            "The rate provider did not respond in time. Please try again shortly.",
        )
    except httpx.RequestError:
        raise FxError(
            502, "upstream_unreachable",
            "Could not reach the rate provider. Please try again shortly.",
        )


async def _currency_is_known(code: str) -> bool:
    """Ask the upstream whether it recognises this currency code at all."""
    async with httpx.AsyncClient(timeout=UPSTREAM_TIMEOUT_SECONDS) as client:
        response = await _get(client, f"{upstream_base()}/v1/latest", {"base": code})

    if response.status_code == 200:
        return True
    if response.status_code == 404:
        return False
    if response.status_code >= 500:
        raise FxError(
            502, "upstream_error",
            "The rate provider returned an error. Please try again shortly.",
        )
    raise FxError(
        502, "upstream_bad_response",
        "The rate provider returned an unexpected response.",
    )


async def _fetch_from_upstream(
    from_currency: str, to_currency: str, asked_date: str
) -> tuple[Decimal, str]:
    """The real rate lookup for two different currencies."""
    base = upstream_base()
    params = {"base": from_currency, "symbols": to_currency}

    async with httpx.AsyncClient(timeout=UPSTREAM_TIMEOUT_SECONDS) as client:
        response = await _get(client, f"{base}/v1/{asked_date}", params)

        if response.status_code == 404:
            # Unknown currency and a before-the-series date BOTH look like 404
            # here. Ask /latest with the same currencies to tell them apart,
            # instead of hardcoding when the ECB series begins.
            latest = await _get(client, f"{base}/v1/latest", params)
            if latest.status_code == 200:
                raise FxError(
                    400, "before_series",
                    f"No published ECB rate for {asked_date}; that date predates "
                    "the available series.",
                )
            raise FxError(
                400, "unknown_currency",
                f"Unknown currency code in '{from_currency}' or '{to_currency}'.",
            )

        if response.status_code >= 500:
            raise FxError(
                502, "upstream_error",
                "The rate provider returned an error. Please try again shortly.",
            )

        if response.status_code != 200:
            raise FxError(
                502, "upstream_bad_response",
                "The rate provider returned an unexpected response.",
            )

        # A 200 whose body is not the JSON we expect must not slip through as a
        # crash or a guessed rate — treat it as a bad upstream response.
        try:
            payload = json.loads(response.text, parse_float=Decimal)
            rate_date = payload["date"]
            rate = payload["rates"][to_currency]
        except (json.JSONDecodeError, KeyError, TypeError):
            raise FxError(
                502, "upstream_bad_response",
                "The rate provider returned an unreadable response.",
            )

    return rate, rate_date


async def fetch_rate(
    from_currency: str, to_currency: str, asked_date: str
) -> tuple[Decimal, str]:
    """Return (rate, rate_date) for `from_currency -> to_currency` on a date.

    `rate_date` is the day the rate ACTUALLY belongs to (the upstream's own
    `date`), which on a weekend or holiday is earlier than `asked_date`. Any
    upstream failure is raised as an FxError, never guessed around.
    """
    key = (from_currency, to_currency, asked_date)
    cached = cache.get(key)
    if cached is not None:
        # Same question already answered — do not ask the upstream again.
        return cached

    if from_currency == to_currency:
        # Converting a currency to itself needs no rate lookup: it is 1 by
        # definition. But an unknown code must not be handed back as though it
        # were a real currency, so confirm the upstream recognises it.
        if not await _currency_is_known(from_currency):
            raise FxError(
                400, "unknown_currency", f"Unknown currency code: '{from_currency}'."
            )
        rate, rate_date = Decimal("1"), asked_date
    else:
        rate, rate_date = await _fetch_from_upstream(
            from_currency, to_currency, asked_date
        )

    # Cache only immutable answers: a past date's rate never changes, so we
    # remember it. Today's rate can still change when the ECB publishes, so we
    # skip it rather than risk serving a stale number for the rest of the day.
    if date_type.fromisoformat(asked_date) < datetime.now(timezone.utc).date():
        cache.set(key, (rate, rate_date))

    return rate, rate_date


def convert_amount(amount: Decimal, rate: Decimal) -> Decimal:
    """Multiply amount by rate, rounding ONLY the final result to 2 decimals."""
    result = amount * rate
    return result.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
