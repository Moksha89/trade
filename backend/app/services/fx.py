"""Currency conversion for position sizing.

The account is denominated in one currency (e.g. AED) while instruments may be
quoted in another (e.g. US100/US500/Gold are quoted in USD). To size a position
from an AED risk budget we must convert a one-point move in the instrument's
quote currency into the account currency.

AED is hard-pegged to USD at 3.6725, so for the v1 instruments (all USD-quoted)
this is exact. A small pegged/static table covers the common pairs; anything not
covered falls back to 1.0 (no conversion) and the caller flags it.
"""

from __future__ import annotations

# account_ccy per 1 unit of quote_ccy
_RATES: dict[tuple[str, str], float] = {
    ("USD", "AED"): 3.6725,
    ("AED", "USD"): 1.0 / 3.6725,
    ("USD", "USD"): 1.0,
    ("AED", "AED"): 1.0,
}


def quote_to_account(quote_ccy: str, account_ccy: str) -> float | None:
    """Return account-currency value of 1 unit of quote currency.

    Returns None when the pair is unknown so the caller can decide how to
    handle it (the sizing path falls back to 1.0 and flags the proposal).
    """
    q = (quote_ccy or "").upper()
    a = (account_ccy or "").upper()
    if not q or not a or q == a:
        return 1.0
    return _RATES.get((q, a))
