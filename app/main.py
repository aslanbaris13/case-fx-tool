"""FastAPI application entry point for the FX conversion tool.

Exposes exactly one endpoint, /tools/convert — the single tool an AI agent
calls to convert an amount between two currencies on a given date.
"""

from datetime import date as date_type, datetime, timezone
from decimal import Decimal, InvalidOperation

from fastapi import FastAPI, Query

from fastapi.exceptions import RequestValidationError

from app import fx
from app.errors import FxError, fx_error_handler, validation_error_handler

app = FastAPI(title="fx-tool", version="0.1.0")
app.add_exception_handler(FxError, fx_error_handler)
app.add_exception_handler(RequestValidationError, validation_error_handler)

SOURCE = "ECB via frankfurter.dev"


@app.get("/tools/convert")
async def convert(
    amount: str | None = Query(default=None),
    from_currency: str = Query(alias="from"),
    to: str = Query(),
    date: str = Query(),
) -> dict:
    """Convert `amount` from one currency to another on a given date.

    Validates the inputs we can judge without the upstream, then asks the
    upstream for the rate. `rate_date` is the day the rate really belongs to;
    `asked_date` echoes what the caller asked for.
    """
    from_currency = from_currency.upper()
    to = to.upper()

    # 1) amount: required, a valid number, positive, at most 2 decimal places.
    #    Built from the string with Decimal so no float ever touches the money.
    if amount is None:
        raise FxError(400, "invalid_amount", "Amount is required.")
    try:
        amount_dec = Decimal(amount)
    except InvalidOperation:
        raise FxError(400, "invalid_amount", f"'{amount}' is not a valid amount.")
    if amount_dec <= 0:
        raise FxError(400, "invalid_amount", "Amount must be greater than zero.")
    if -amount_dec.as_tuple().exponent > 2:
        raise FxError(400, "invalid_amount", "Amount cannot have more than two decimal places.")

    # 2) date: must be a real calendar date and not in the future.
    try:
        asked = date_type.fromisoformat(date)
    except ValueError:
        raise FxError(400, "invalid_date", f"'{date}' is not a valid date (use YYYY-MM-DD).")
    today = datetime.now(timezone.utc).date()
    if asked > today:
        raise FxError(400, "future_date", f"No rate exists yet for {date}; that date is in the future.")

    # 3) same currency: the rate is 1 by definition — no upstream call needed.
    if from_currency == to:
        return {
            "amount": float(amount_dec), "from": from_currency, "to": to,
            "rate": 1.0, "result": float(amount_dec),
            "rate_date": date, "asked_date": date,
            "is_fallback": False, "note": None, "source": SOURCE,
        }

    rate, rate_date = await fx.fetch_rate(from_currency, to, date)
    result = fx.convert_amount(amount_dec, rate)

    is_fallback = rate_date != date
    note = None
    if is_fallback:
        note = (
            f"No ECB rate was published for {date}; "
            f"used the most recent rate, from {rate_date}."
        )

    return {
        "amount": float(amount_dec), "from": from_currency, "to": to,
        "rate": float(rate), "result": float(result),
        "rate_date": rate_date, "asked_date": date,
        "is_fallback": is_fallback, "note": note, "source": SOURCE,
    }
