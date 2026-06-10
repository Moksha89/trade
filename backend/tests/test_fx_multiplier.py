"""Currency-multiplier resolution for non-USD-quoted instruments.

Regression for the J225 phantom P/L: a JPY-quoted instrument had no entry in
the static FX peg table, so `risk_unit_multiplier` returned 0, sizing recorded
`initial_risk_aed = 0`, and `_ccy_mult` then fell back to 1.0 — valuing a 1 JPY
point move as 1 AED (~44x overstated). The live USD cross fixes the multiplier.
"""

from types import SimpleNamespace

from app.broker.capital import CapitalClient


def _client(monkeypatch, quote_ccy, quotes):
    c = CapitalClient(api_key="k", identifier="i", password="p", base_url="http://x")
    monkeypatch.setattr(c, "account_currency", lambda: "AED")
    monkeypatch.setattr(c, "instrument_meta", lambda inst: (quote_ccy, 1.0))

    def fake_quote(sym):
        if sym in quotes:
            bid, ask = quotes[sym]
            return SimpleNamespace(bid=bid, ask=ask)
        raise RuntimeError(f"no market {sym}")

    monkeypatch.setattr(c, "get_quote", fake_quote)
    return c


def test_usd_quoted_uses_static_peg(monkeypatch):
    # USD-quoted instrument must stay exact on the AED peg (no live cross).
    c = _client(monkeypatch, "USD", quotes={})
    assert abs(c.risk_unit_multiplier("GOLD") - 3.6725) < 1e-9


def test_jpy_quoted_resolves_via_usdjpy(monkeypatch):
    # JPY->AED = (1/USDJPY) * USD->AED. USDJPY=160.55 -> ~0.02288 AED/point.
    c = _client(monkeypatch, "JPY", quotes={"USDJPY": (160.544, 160.556)})
    mult = c.risk_unit_multiplier("J225")
    assert abs(mult - (3.6725 / 160.55)) < 1e-4
    # And it's nowhere near the broken 1.0 fallback.
    assert mult < 0.05


def test_eur_quoted_resolves_via_eurusd(monkeypatch):
    # EUR->AED = EURUSD * USD->AED. EURUSD=1.1538 -> ~4.237 AED/point.
    c = _client(monkeypatch, "EUR", quotes={"EURUSD": (1.15376, 1.15383)})
    assert abs(c.risk_unit_multiplier("DE40") - (1.153795 * 3.6725)) < 1e-3


def test_unknown_currency_returns_zero_not_one(monkeypatch):
    # No FX market available -> 0 (cannot size / value), never a fabricated 1.0.
    c = _client(monkeypatch, "XYZ", quotes={})
    assert c.risk_unit_multiplier("WHATEVER") == 0.0
