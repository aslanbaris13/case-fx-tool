"""Upstream (Frankfurter) access and the currency conversion itself.

Happy path only for now: this step assumes valid inputs and a healthy
upstream. Input validation, edge cases and structured error handling arrive
in a later step.
"""

from __future__ import annotations

import json
import os
from decimal import ROUND_HALF_UP, Decimal

import httpx

DEFAULT_UPSTREAM_BASE = "https://api.frankfurter.dev"

# How long we wait for the upstream before giving up. Turning a timeout into a
# clean error is a later step; here we just set it explicitly instead of
# waiting forever.
UPSTREAM_TIMEOUT_SECONDS = 10.0


def upstream_base() -> str:
    """Base URL of the upstream FX API, read from the environment.

    Never hardcoded: reviewers point FX_UPSTREAM_BASE at a fake upstream, so
    the real host must be a changeable default, not baked into the code.
    """
    return os.getenv("FX_UPSTREAM_BASE", DEFAULT_UPSTREAM_BASE)


async def fetch_rate(
    from_currency: str, to_currency: str, asked_date: str
) -> tuple[Decimal, str]:
    """Ask the upstream for `from_currency -> to_currency` on `asked_date`.

    Returns (rate, rate_date):
      - rate: the exchange rate as a Decimal, parsed straight from the JSON
        text so that no lossy float ever touches the money.
      - rate_date: the date the rate ACTUALLY belongs to, read from the
        upstream's own `date` field (may be an earlier business day than
        `asked_date` on weekends/holidays).
    """
    url = f"{upstream_base()}/v1/{asked_date}"
    params = {"base": from_currency, "symbols": to_currency}

    async with httpx.AsyncClient(timeout=UPSTREAM_TIMEOUT_SECONDS) as client:
        response = await client.get(url, params=params)
        response.raise_for_status()
        # parse_float=Decimal: read every JSON number as a Decimal directly,
        # instead of letting json turn "56.1718" into a lossy float first.
        payload = json.loads(response.text, parse_float=Decimal)

    rate_date = payload["date"]
    rate = payload["rates"][to_currency]  # already a Decimal thanks to parse_float
    return rate, rate_date


def convert_amount(amount: Decimal, rate: Decimal) -> Decimal:
    """Multiply amount by rate, rounding ONLY the final result to 2 decimals."""
    result = amount * rate
    return result.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
