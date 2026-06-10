"""On-read live pricing: marks fluctuate every poll, not only every 30s."""

from app.services import live_pricing
from app.services.live_pricing import live_mark, mark_values


class _Trade:
    def __init__(self, direction):
        self.instrument = "GOLD"
        self.direction = direction
        self.size = 2.0
        self.entry_price = 100.0
        self.initial_risk_per_unit = 1.0
        self.initial_risk_aed = 2.0  # → _ccy_mult = 2/(2*1) = 1.0
        self.current_price = 0.0
        self.unrealized_pl = 0.0


def test_mark_values_long_uses_bid():
    # Long marks to bid; profit when bid > entry.
    price, upl = mark_values("long", 2.0, 100.0, 1.0, bid=101.0, ask=101.2)
    assert price == 101.0
    assert upl == 2.0  # 2 * (101-100) * 1


def test_mark_values_short_uses_ask():
    price, upl = mark_values("short", 2.0, 100.0, 1.0, bid=98.8, ask=99.0)
    assert price == 99.0
    assert upl == 2.0  # 2 * (100-99) * 1


def test_live_mark_updates_trade_in_memory(monkeypatch):
    monkeypatch.setattr(live_pricing, "_quote_bid_ask", lambda inst: (105.0, 105.5))
    t = _Trade("long")
    live_mark(t)
    assert t.current_price == 105.0
    assert t.unrealized_pl == 10.0  # 2 * (105-100) * 1


def test_live_mark_swallows_quote_errors(monkeypatch):
    def boom(inst):
        raise RuntimeError("broker down")

    monkeypatch.setattr(live_pricing, "_quote_bid_ask", boom)
    t = _Trade("long")
    t.current_price = 99.0
    t.unrealized_pl = -2.0
    live_mark(t)  # must not raise; keeps stale values
    assert t.current_price == 99.0
    assert t.unrealized_pl == -2.0


def test_quote_cache_hits_within_ttl(monkeypatch):
    calls = {"n": 0}

    class _Q:
        bid = 10.0
        ask = 10.2

    class _Prov:
        def get_quote(self, inst):
            calls["n"] += 1
            return _Q()

    import app.market_data.factory as factory

    monkeypatch.setattr(factory, "get_provider", lambda: _Prov())
    live_pricing._quote_cache.clear()
    a = live_pricing._quote_bid_ask("XYZ")
    b = live_pricing._quote_bid_ask("XYZ")  # cached → no second provider call
    assert a == b == (10.0, 10.2)
    assert calls["n"] == 1
