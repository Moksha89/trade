"""Broker→DB import: positions opened outside the bot (e.g. manually in the
Capital.com app) must be adopted so they show on the dashboard, count toward the
risk caps, and are managed like bot trades."""

from app.db import SessionLocal, init_db
from app.models import Trade
from app.services import accounts, engine

init_db()

FX = 3.6725


class _Provider:
    def risk_unit_multiplier(self, instrument):
        return FX


class _Exec:
    mode = "live"


def _broker_row(deal_id, direction, size, level, stop, limit=None, epic="BTCUSD"):
    return {
        "position": {
            "dealId": deal_id, "direction": direction, "size": size,
            "level": level, "stopLevel": stop, "limitLevel": limit,
            "dealReference": "ref-" + deal_id,
        },
        "market": {"epic": epic, "instrumentName": "Bitcoin/USD"},
    }


def _clear(db):
    for t in db.query(Trade).all():
        db.delete(t)
    db.commit()


def test_adopts_untracked_position_with_aed_risk():
    db = SessionLocal()
    _clear(db)
    deals = {"D1": _broker_row("D1", "SELL", 0.048, 60700.0, 61920.0)}
    engine._adopt_untracked_positions(db, _Provider(), _Exec(), deals)

    opens = accounts.open_trades(db)
    assert len(opens) == 1
    t = opens[0]
    assert t.instrument == "BTCUSD" and t.direction == "short"
    assert t.deal_id == "D1" and t.strategy == "manual"
    assert t.entry_price == 60700.0 and t.stop_loss == 61920.0
    # risk_aed = size * stop_distance * FX
    assert abs(t.initial_risk_aed - 0.048 * 1220.0 * FX) < 0.5
    # combined open risk reported in AED
    assert abs(accounts.current_open_risk(db) - t.initial_risk_aed) < 0.5
    _clear(db)


def test_does_not_double_adopt_known_deal():
    db = SessionLocal()
    _clear(db)
    deals = {"D2": _broker_row("D2", "BUY", 0.05, 100.0, 95.0, epic="US100")}
    engine._adopt_untracked_positions(db, _Provider(), _Exec(), deals)
    engine._adopt_untracked_positions(db, _Provider(), _Exec(), deals)
    assert len(accounts.open_trades(db)) == 1
    _clear(db)


def test_instrument_from_epic_uses_epic():
    assert engine._instrument_from_epic("BTCUSD") == "BTCUSD"
    assert engine._instrument_from_epic("GOLD") == "GOLD"
    assert engine._instrument_from_epic("US100") == "US100"
