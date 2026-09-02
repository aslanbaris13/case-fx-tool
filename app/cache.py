"""A tiny in-process cache for upstream lookups.

Keyed by (from, to, date) — the three things that fully determine a rate. A
past date's rate is fixed forever, so it is safe to remember and never ask the
upstream for again. Today's rate can still change when the ECB publishes, so we
deliberately do NOT cache today (see fx.fetch_rate).
"""

from __future__ import annotations

from decimal import Decimal

Key = tuple[str, str, str]          # (from_currency, to_currency, asked_date)
Value = tuple[Decimal, str]         # (rate, rate_date)

_store: dict[Key, Value] = {}


def get(key: Key) -> Value | None:
    """Return the cached (rate, rate_date) for this key, or None on a miss."""
    return _store.get(key)


def set(key: Key, value: Value) -> None:
    """Remember (rate, rate_date) for this key."""
    _store[key] = value


def clear() -> None:
    """Empty the cache (used to isolate tests)."""
    _store.clear()
