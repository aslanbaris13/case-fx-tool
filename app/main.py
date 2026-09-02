"""FastAPI application entry point for the FX conversion tool.

Exposes /tools/convert, the single endpoint an AI agent calls as a tool.
This step covers the happy path only; edge cases and errors come later.
"""

from decimal import Decimal

from fastapi import FastAPI, Query

from app import fx

app = FastAPI(title="fx-tool", version="0.1.0")


@app.get("/health")
def health() -> dict:
    """Liveness check — used to confirm the service is up."""
    return {"status": "ok"}


@app.get("/tools/convert")
async def convert(
    amount: Decimal = Query(),
    from_currency: str = Query(alias="from"),
    to: str = Query(),
    date: str = Query(),
) -> dict:
    """Convert `amount` from one currency to another on a given date.

    `from` is a reserved word in Python, so it is received under the alias
    `from_currency`. `rate_date` is the day the rate really belongs to
    (from the upstream), while `asked_date` echoes what the caller asked for.
    """
    rate, rate_date = await fx.fetch_rate(from_currency, to, date)
    result = fx.convert_amount(amount, rate)

    # Make the "nearest earlier business day" fallback visible to the caller.
    # The upstream walks back to the last day it has a rate for; when that day
    # differs from the one asked, the model must be able to tell the customer.
    is_fallback = rate_date != date
    note = None
    if is_fallback:
        note = (
            f"No ECB rate was published for {date}; "
            f"used the most recent rate, from {rate_date}."
        )

    return {
        "amount": float(amount),
        "from": from_currency,
        "to": to,
        "rate": float(rate),
        "result": float(result),
        "rate_date": rate_date,
        "asked_date": date,
        "is_fallback": is_fallback,
        "note": note,
        "source": "ECB via frankfurter.dev",
    }
