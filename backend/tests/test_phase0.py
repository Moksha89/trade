"""Phase 0 smoke tests: health endpoint + admin login flow."""

from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_health():
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"


def test_login_rejects_bad_credentials():
    resp = client.post(
        "/api/auth/login", data={"username": "admin", "password": "nope"}
    )
    assert resp.status_code == 401


def test_login_and_protected_route():
    resp = client.post(
        "/api/auth/login", data={"username": "admin", "password": "secret123"}
    )
    assert resp.status_code == 200
    token = resp.json()["access_token"]

    me = client.get("/api/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert me.status_code == 200
    assert me.json()["username"] == "admin"


def test_protected_route_requires_auth():
    resp = client.get("/api/dashboard/status")
    assert resp.status_code == 401
