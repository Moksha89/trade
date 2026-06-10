"""FastAPI application entrypoint.

Phase 0: health endpoint + admin JWT login + a dashboard status stub.
"""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api import auth as auth_routes
from app.api import system as system_routes
from app.api import trading as trading_routes
from app.config import settings
from app.db import init_db

app = FastAPI(title="AI Trading System API", version="0.1.0")


@app.on_event("startup")
def _startup() -> None:
    init_db()

_origins = [o.strip() for o in settings.cors_origins.split(",") if o.strip()]
app.add_middleware(
    CORSMiddleware,
    allow_origins=_origins or ["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(system_routes.router)
app.include_router(auth_routes.router)
app.include_router(trading_routes.router)


@app.get("/")
def root() -> dict[str, str]:
    return {"service": "ai-trading-system", "docs": "/docs", "health": "/health"}
