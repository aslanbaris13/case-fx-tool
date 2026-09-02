"""Structured, client-safe errors.

Every non-2xx response shares one shape: a short machine `error` code plus a
human `message` the calling model can relay to the customer. Raising FxError
anywhere in the request turns into that JSON, via the handler registered on
the app in main.py — so no code path ever crashes with a raw 500.
"""

from __future__ import annotations

from fastapi import Request
from fastapi.responses import JSONResponse


class FxError(Exception):
    """An error we can safely show the caller.

    status_code: the HTTP status to return (4xx = caller's fault, 5xx = upstream).
    error:       a short machine code the agent can branch on (e.g. future_date).
    message:     a sentence a person could read, for the model to pass on.
    """

    def __init__(self, status_code: int, error: str, message: str) -> None:
        self.status_code = status_code
        self.error = error
        self.message = message
        super().__init__(message)


async def fx_error_handler(_: Request, exc: FxError) -> JSONResponse:
    """Render any FxError as `{error, message}` with its HTTP status."""
    return JSONResponse(
        status_code=exc.status_code,
        content={"error": exc.error, "message": exc.message},
    )
