"""Capital.com broker integration (REST + WebSocket)."""

from app.broker.capital import CapitalClient, CapitalError

__all__ = ["CapitalClient", "CapitalError"]
