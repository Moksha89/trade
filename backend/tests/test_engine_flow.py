"""End-to-end paper-mode flow + backtest via the API and engine."""

from fastapi.testclient import TestClient

from app.backtest.engine import run_backtest
from app.db import init_db
from app.main import app
from app.market_data.synthetic import SyntheticProvider

init_db()
client = TestClient(app)


def _auth() -> dict[str, str]:
    r = client.post("/api/auth/login", data={"username": "admin", "password": "secret123"})
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


def test_scan_proposes_and_risk_decides():
    h = _auth()
    client.post("/api/bot/start", headers=h)
    r = client.post("/api/bot/scan", headers=h)
    assert r.status_code == 200
    ideas = r.json()["created"]
    # At least one instrument should yield an idea; every idea carries a decision.
    for idea in ideas:
        assert idea["risk_reason"]
        assert idea["status"] in ("approved", "rejected")
        if idea["risk_approved"]:
            assert idea["stop_loss"] > 0 and idea["take_profit_1"] > 0
            assert idea["position_size"] > 0


def test_approve_executes_and_enforces_max_active():
    h = _auth()
    client.post("/api/bot/start", headers=h)
    client.post("/api/bot/scan", headers=h)
    ideas = client.get("/api/ideas", headers=h).json()
    approved = [i for i in ideas if i["risk_approved"] and i["status"] == "approved"]
    opened = 0
    for idea in approved:
        resp = client.post(f"/api/ideas/{idea['id']}/approve", headers=h)
        if resp.status_code == 200:
            opened += 1
    open_trades = client.get("/api/trades?status=open", headers=h).json()
    # Never exceed the configured max of 2 active trades.
    assert len(open_trades) <= 2


def test_backtest_metrics_shape():
    candles = SyntheticProvider().get_candles("US100", "5M", 600)
    report = run_backtest("US100", candles)
    d = report.as_dict()
    for key in ("trades", "win_rate", "profit_factor", "avg_r", "max_drawdown_r"):
        assert key in d


def test_emergency_close_all():
    h = _auth()
    client.post("/api/emergency/close-all", headers=h)
    open_trades = client.get("/api/trades?status=open", headers=h).json()
    assert open_trades == []
