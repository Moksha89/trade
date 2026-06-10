# AI Trading System (Capital.com)

A Capital.com CFD trading agent that runs **24/7 on a Linux VPS** and exposes a secure
web dashboard on the VPS public IP.

**Core principle:** the AI (Claude) **only proposes** trades; a **deterministic Python
risk engine is the final authority** that approves or rejects every trade. Risk control
takes priority over AI prediction — the AI cannot bypass the risk engine.

## Highlights

- **Broker:** Capital.com REST (session/account/orders/positions/confirms) + WebSocket
  (live prices). Demo account first; Live mode disabled by default.
- **AI:** Claude returns strict JSON proposals only; OpenAI optional later.
- **Risk:** max 2 active trades; every trade has entry + stop-loss + take-profit before
  execution; daily/weekly loss limits; no martingale / averaging down / revenge trading.
- **Dynamic management:** SL→breakeven, profit lock, partial close, trailing.
- **Hedging:** available but **OFF by default**.
- **Dashboard:** Next.js UI with admin login, live status, trade ideas, open trades,
  risk/strategy/AI settings, journal, backtests, audit logs, and an **emergency kill switch**.
- **Telegram:** alerts + approvals (`/status`, `/halt`, `/resume_demo`, `/disable_auto`).
- **Deployment:** Docker Compose + Nginx on Ubuntu VPS. DB/Redis/internal services
  never exposed publicly; firewall locked to 80/443 (+22).

## Documentation

- **Full build specification:** [`docs/BUILD_SPEC.md`](docs/BUILD_SPEC.md) — scope,
  architecture, risk rules, AI JSON schema, bot loop, dashboard screens, phases, and
  acceptance criteria.

## Status

Phase 0 (spec + scaffolding). See the phased delivery plan in the build spec.

## Configuration

Copy `.env.example` to `.env` and fill in credentials. **Never commit `.env`.**
