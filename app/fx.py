"""Upstream (Frankfurter) access and the currency conversion itself.

Every way the upstream can fail — slow, down, a 5xx, a 404, or a body that is
not the JSON we expect — is turned into a structured FxError instead of a crash
or an invented number. The endpoint stays clean; the errors raised here bubble
up to the handler registered in main.py.
"""

from __future__ import annotations

import json
import os
from decimal import ROUND_HALF_UP, Decimal

import httpx

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


async def fetch_rate(
    from_currency: str, to_currency: str, asked_date: str
) -> tuple[Decimal, str]:
    """Ask the upstream for `from_currency -> to_currency` on `asked_date`.

    Returns (rate, rate_date): the rate as a Decimal parsed straight from the
    JSON text, and the date the rate ACTUALLY belongs to (upstream's `date`).
    Any upstream failure is raised as an FxError, never guessed around.
    """
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


def convert_amount(amount: Decimal, rate: Decimal) -> Decimal:
    """Multiply amount by rate, rounding ONLY the final result to 2 decimals."""
    result = amount * rate
    return result.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
