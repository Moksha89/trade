# AI Trading System — Build Specification

> **Status:** Final requirements locked (v1). This document is the single source of
> truth for scope, architecture, risk rules, and phased delivery.
>
> **One-line summary:** A Capital.com CFD trading agent that runs 24/7 on a Linux VPS,
> where an LLM (Claude) **only proposes** trades and a **deterministic Python risk
> engine is the final authority** on every execution. A secure web dashboard (served
> on the VPS public IP) plus Telegram provide monitoring, approvals, and an emergency
> kill switch.

---

## 1. Product Principles (non-negotiable)

1. **Risk control > AI prediction.** This is a CFD system. The AI cannot bypass the
   risk engine under any circumstance.
2. **AI proposes, risk engine disposes.** Every trade passes through a deterministic
   Python risk engine that can approve or reject. The risk engine has no LLM in its
   decision path.
3. **No SL/TP → no trade.** Every order must have entry, stop-loss, and take-profit
   defined *before* execution. If a server-side stop-loss cannot be attached, the
   position is closed/cancelled immediately and an alert is raised.
4. **Hard caps always enforced:** max **2** active live trades; **no martingale, no
   averaging down, no revenge trading**; daily/weekly loss limits halt trading.
5. **Demo first.** Live mode is disabled by default and only unlocked after demo +
   forward-test results. Auto mode stays off until explicitly enabled.
6. **Everything is logged.** Every market scan, indicator calc, AI prompt/response,
   risk decision, order action, and manual override is written to an audit trail.
7. **Secrets never committed.** All credentials live in `.env` / environment only.

---

## 2. Tech Stack

| Layer | Choice |
|---|---|
| Backend | Python 3.11+, FastAPI, Pydantic v2, Uvicorn |
| Frontend | Next.js / React dashboard |
| Database | PostgreSQL (prod). SQLite allowed for the earliest prototype only |
| Queue / scheduler | Redis + APScheduler (Celery as an option if workloads grow) |
| Broker | Capital.com REST (session/account/orders/positions/confirms) + WebSocket (live prices) |
| AI provider | Claude API first; OpenAI optional later as a second opinion |
| Deployment | Docker Compose on Ubuntu VPS |
| Reverse proxy | Nginx (HTTP on VPS IP first; domain + SSL later) |
| Logging | Structured JSON logs |
| Alerts | Telegram bot |
| Secrets | `.env` files / environment variables, never committed |

---

## 3. Deployment & VPS Security Model

- All services run on the VPS via Docker Compose.
- Dashboard reachable at `http://VPS_IP` initially; add domain + Let's Encrypt SSL later.
- **Firewall (ufw/security group):** expose only `80/443` (and `22` for SSH). Everything
  else is bound to the internal Docker network / `127.0.0.1`.
- **Not exposed publicly:** PostgreSQL, Redis, the broker-facing internal services, the
  FastAPI app port (only reachable via Nginx).
- Dashboard requires **admin login** (JWT/session auth, hashed passwords).
- **Emergency kill switch** available from the dashboard and Telegram.

### Container topology (Docker Compose services)
```
nginx          → reverse proxy, only public-facing service (80/443)
frontend       → Next.js dashboard (internal)
api            → FastAPI app: REST API + auth + dashboard backend (internal)
worker         → bot loop / scheduler (APScheduler) + trade manager (internal)
db             → PostgreSQL (internal only, named volume)
redis          → Redis (internal only)
```

---

## 4. High-Level Architecture

```
                         ┌──────────────────────────────────────┐
                         │            Nginx (80/443)             │
                         └───────────────┬──────────────────────┘
                          ┌──────────────┴───────────────┐
                          ▼                               ▼
                 ┌─────────────────┐            ┌───────────────────┐
                 │  Next.js (UI)   │  REST/WS   │  FastAPI (api)     │
                 │  dashboard      │◀──────────▶│  auth, REST, SSE   │
                 └─────────────────┘            └─────────┬─────────┘
                                                          │ shared DB/Redis
   ┌──────────────────────────────────────────────────────┴───────────────────────┐
   │  worker (bot loop)                                                             │
   │                                                                                │
   │  Market Data ─▶ Indicator Engine ─▶ Market Classifier ─▶ AI Proposal (Claude)  │
   │                                                  │                             │
   │                                                  ▼                             │
   │                                          RISK ENGINE (deterministic)           │
   │                                          approve / reject / size               │
   │                                                  │                             │
   │                       ┌──────────────────────────┴───────────────┐            │
   │                       ▼                                           ▼            │
   │             Execution (paper/demo/live)                 Trade Manager (SL/TP)  │
   └────────────────────────────┬───────────────────────────────────┬─────────────┘
                                 ▼                                   ▼
                       Capital.com REST/WS                    Telegram alerts
```

---

## 5. Component Specifications

### 5.1 Market Data Layer
- **Capital.com REST** — historical candles (multi-timeframe).
- **Capital.com WebSocket** — live prices / quotes streaming.
- **Capital.com client sentiment** endpoint.
- **News / economic calendar** provider (high-impact event awareness).
- **Spread & market-open/tradable status** checker per instrument.

### 5.2 Indicator Layer (deterministic Python only — never the LLM)
All indicators are computed in Python and passed to the AI as prepared data:
- EMA 20 / EMA 50 / EMA 200
- RSI
- MACD
- ATR
- VWAP (if available for instrument/session)
- Support & resistance levels
- Swing high / swing low
- Volume / volatility filter
- Spread filter

### 5.3 AI Analysis Layer (Claude)
Claude receives **only prepared structured data** and must return **strict JSON only**.

Input payload to the model:
- Instrument
- Current price
- Multi-timeframe indicator data
- Support / resistance levels
- Client sentiment
- News / calendar risk
- Current open positions
- Existing exposure
- Available risk budget

**AI output JSON schema (strict):**
```json
{
  "instrument": "US100",
  "direction": "long | short | no_trade",
  "strategy": "trend_pullback | breakout_retest | breakdown_retest | range_reversal | momentum_continuation | no_trade",
  "entry_type": "market | limit | stop",
  "entry_price": 0,
  "stop_loss": 0,
  "take_profit_1": 0,
  "take_profit_2": 0,
  "confidence": 0,
  "risk_reward": 0,
  "position_size": 0,
  "rationale": "short explanation",
  "invalidation_condition": "when this setup becomes invalid",
  "risk_flags": [],
  "management_plan": {
    "move_sl_to_breakeven_at_R": 1.0,
    "lock_profit_at_R": 1.5,
    "partial_close_at_R": 2.0,
    "partial_close_percent": 50,
    "trailing_method": "swing | ema20 | atr"
  }
}
```
- Responses are validated against a Pydantic schema. Malformed JSON → reject + log;
  never executed.
- `position_size` from the AI is **advisory only**; the risk engine recomputes the
  authoritative size from risk budget and SL distance.

### 5.4 Risk Engine (deterministic Python — FINAL AUTHORITY)
The risk engine is the last gate before any order. The AI **cannot** override it.

**Default account assumption:** Starting capital **AED 5,000**.

**Default risk settings (configurable in Risk Settings):**
| Setting | Default |
|---|---|
| Max active trades | 2 |
| Max risk per trade | AED 25–50 |
| Total combined open risk | max AED 100 |
| Max trades per day | 2–3 |
| Daily max loss | AED 100–150 |
| Weekly max loss | AED 300–400 |
| Stop trading after | 2 losing trades in one day |
| Minimum risk/reward | 1:2 |
| Minimum confidence | 70% |

**Rejection rules (any one → reject):**
- Trend unclear / market choppy / price mid-range
- Risk/reward below 1:2
- Spread too high
- High-impact news nearby
- Market closed / not tradable
- Stop-loss distance too wide
- Daily loss limit reached / weekly loss limit reached
- Already 2 active trades open
- Existing exposure too high
- Confidence below threshold (70%)
- Duplicate trade on the same instrument
- Any martingale / averaging-down / size-increase-after-loss pattern detected

Every decision returns a structured result `{approved: bool, reason: str, computed_size, computed_risk_aed}` and is written to the audit log.

### 5.5 Allowed Instruments (v1)
- US100, US500, Gold
- EUR/USD (demo only, optional)
- GBP/USD (demo only, optional)
- BTC/USD (demo only, **not live** initially)

**Live trading v1 starts only with:** US100, US500, Gold.

### 5.6 Market Classification (before requesting a trade)
Each instrument is classified into exactly one:
1. Bullish trend
2. Bearish trend
3. Range-bound
4. Breakout condition
5. Breakdown condition
6. Momentum condition
7. Choppy / no trade
8. News-risk / no trade

### 5.7 Strategy Rules
- **Bullish:** Trend Pullback Buy, Breakout Retest Buy
- **Bearish:** Trend Pullback Sell, Breakdown Retest Sell
- **Sideways:** Range Reversal Buy near support, Range Reversal Sell near resistance
- **Momentum:** Momentum continuation only with smaller risk; demo first

**No-trade logic** — reject when: trend unclear, choppy, mid-range, R:R < 1:2, spread
too high, high-impact news nearby, daily loss limit reached, already 2 active trades,
exposure too high, or confidence below threshold.

---

## 6. 24/7 Bot Loop

The worker runs continuously with three cadences:

**Every 30s–1m (health & live-trade management):**
- Check Capital.com API connection
- Check account balance
- Check open positions
- Check order status
- Check market open / tradable status
- Manage existing live trades; move SL/TP when rules trigger
- Log health status

**Every 5m (scan & propose):**
- Scan allowed instruments
- Calculate indicators
- Classify market condition
- Only if fewer than 2 active trades: ask AI for a proposal **only when a setup may be valid**
- Run risk engine → create trade idea or reject with reason

**Every 15m (reporting):**
- Summarize bot state
- Save journal snapshot
- Send Telegram update if needed

---

## 7. Live Trade Management

Every opened trade must carry: Entry, Stop-loss, Take-profit 1, Take-profit 2 (if
supported), and a management plan.

After a trade opens:
- **+1R:** move stop-loss to breakeven
- **+1.5R:** move stop-loss to lock +0.5R
- **+2R or TP1 hit:** close 50% (if supported)
- **After TP1:** trail remaining position using selected method (swing / EMA20 / ATR)
- **Reversal signal:** tighten stop-loss or close
- **Setup invalidated:** close position
- **API modification fails:** alert Telegram + dashboard immediately

---

## 8. Hedging (default OFF)

- Add hedge mode, disabled by default.
- Only **one** hedge per instrument.
- Hedge size **max 30%** of original position.
- Hedge only after reversal **confirmation**.
- Hedge only if the risk engine approves.
- Never hedge to repeatedly rescue a losing trade.
- No martingale, no averaging down.
- No hedge if daily loss limit reached.
- Log every hedge decision separately.

---

## 9. Execution Modes

1. **Paper Mode** — no broker orders; log AI proposals and simulated results.
2. **Demo Mode** — Capital.com demo API; place real demo orders; test SL/TP,
   order modification, and trade management.
3. **Approval Mode** — AI proposes → risk engine validates → user approves from
   dashboard or Telegram → order executes.
4. **Live Auto Mode** — **disabled by default**; only after demo results; must obey
   all risk rules; must have emergency stop.

---

## 10. Capital.com Order Behavior

- REST for: session, account, positions, working orders, confirms.
- WebSocket for: live prices.
- Handle session expiry / re-auth automatically.
- Handle rate limits (backoff + monitor).
- Confirm every order via deal confirmation.
- Store every `dealReference` and `dealId`.
- Every position must have a **server-side stop-loss** where supported.
- If stop-loss cannot be attached → cancel/close the trade immediately and alert.

---

## 11. Dashboard (web UI — not just Telegram)

**Screens:**
1. Login
2. Main Dashboard
3. Bot Status
4. AI Trade Ideas
5. Open Trades
6. Orders
7. Risk Settings
8. Strategy Settings
9. AI Settings
10. Capital.com Connection Settings
11. Trade Journal
12. Backtest / Forward Test Results
13. Emergency Panel
14. Logs / Audit Trail

**Main Dashboard shows:** bot running/stopped, mode (Demo/Approval/Live),
Capital.com connection status, account balance, available margin/funds, today P/L,
weekly P/L, open trades count, max active trades allowed, current open risk, daily
loss-limit used, last AI decision, last risk rejection reason, **Emergency Stop button**.

**AI Trade Ideas card:** instrument, direction (Long/Short/No Trade), strategy, entry,
SL, TP1, TP2, risk in AED, R:R, confidence, AI rationale, risk flags. Buttons:
Approve, Reject, Edit, Send to Demo, Cancel.

**Open Trades:** instrument, direction, entry, current price, position size, SL, TP,
current R multiple, unrealized P/L, AI management suggestion, last SL/TP update.
Buttons: Close, Move SL, Move TP, Trail, Hedge (if enabled).

**Risk Settings:** max active trades, max risk per trade, max combined open risk, max
trades/day, daily loss limit, weekly loss limit, stop after N losses, min R:R, min AI
confidence, allowed instruments, spread limit, news filter, market-hours filter,
no-trade session controls.

**Strategy Settings:** allow bullish/bearish ON/OFF; per-strategy ON/OFF (Trend
Pullback, Breakout Retest, Breakdown Retest, Range Reversal, Momentum Continuation);
multi-timeframe confirmation ON/OFF; timeframes (15M/1H/4H/Daily); trailing method
(swing/EMA20/ATR); breakeven trigger; profit-lock trigger; partial close %; auto-close
on reversal ON/OFF.

**AI Settings:** provider (Claude); model selection; confidence threshold; call
frequency; second-opinion model (later); allow AI to create ideas / manage SL-TP /
open trades; require approval before execution; auto-mode toggle (disabled by default).

**Capital.com Settings:** Demo/Live mode; API key status; account ID; reconnect button;
session refresh status; WebSocket status; rate-limit monitor; last API error;
instrument discovery.

**Emergency Panel:** Stop bot; disable auto trading; cancel pending orders; close all
open positions; lock trading for today; disable hedging; disconnect broker API;
Telegram `/halt` support.

---

## 12. Telegram (alerts + approvals, not a dashboard replacement)

Supports: trade proposal alert; Approve/Reject; trade opened alert; SL/TP moved alert;
trade closed alert; daily P/L summary; error alert.

Commands: `/status`, `/halt`, `/resume_demo` (demo only), `/disable_auto`.

---

## 13. Backtesting / Forward Testing (before live)

- Strategy replay on historical candles.
- Forward-test in demo.
- Metrics: win rate, profit factor, max drawdown, average R, number of trades,
  best/worst trade, strategy-wise performance, instrument-wise performance, AI
  confidence vs. actual result.

---

## 14. Audit Logs

Log every event with structured JSON: market scan; indicator calculation; AI prompt
hash; AI response; risk approval/rejection; order creation; order modification; SL/TP
movement; hedge action; close action; API error; manual override; emergency stop.

---

## 15. Phased Delivery Plan

### Phase 0 — Scaffolding
- Updated MD/spec (this document), repo structure, `.env.example`, Docker Compose,
  basic FastAPI health endpoint, basic dashboard login by VPS IP.

### Phase 1 — Capital.com data
- Demo auth, account balance fetch, instrument discovery, historical price fetch,
  WebSocket live price connection, indicator calculation.

### Phase 2 — Brain (paper)
- AI JSON proposal engine, market classification, risk engine, paper-mode trade
  simulation, journal logging.

### Phase 3 — Demo execution
- Demo execution, open/close positions, attach SL/TP, modify SL/TP, deal confirmation,
  Telegram alerts.

### Phase 4 — Full app
- Full dashboard, approval mode, open-trade management, emergency panel, audit logs.

### Phase 5 — Validation
- Backtesting + forward-testing, strategy performance reports, settings tuning.

### Phase 6 — Live (small)
- Live small-size mode, manual approval required, auto mode remains disabled until
  explicitly enabled.

---

## 16. Acceptance Criteria

- Bot runs 24/7 without crashing.
- Dashboard accessible by VPS IP.
- Capital.com demo connection stable.
- Maximum 2 active trades enforced.
- No trade opens without SL/TP.
- Dynamic SL/TP modification works.
- Bullish and bearish trades supported.
- Hedging available but OFF by default.
- Daily loss limit stops trading.
- Emergency stop works.
- Every action is logged.
- Paper/demo testing completed before live.

---

## 17. Proposed Repository Structure

```
trade/
├── docker-compose.yml
├── .env.example
├── README.md
├── docs/
│   └── BUILD_SPEC.md            # this document
├── backend/
│   ├── pyproject.toml
│   ├── app/
│   │   ├── main.py              # FastAPI app + health endpoint
│   │   ├── config.py            # settings from env (.env)
│   │   ├── auth/                # admin login, JWT/session
│   │   ├── api/                 # REST routers (dashboard, settings, trades, emergency)
│   │   ├── broker/              # Capital.com REST + WebSocket client
│   │   ├── market_data/         # candles, sentiment, news/calendar, spread/status
│   │   ├── indicators/          # EMA/RSI/MACD/ATR/VWAP/S&R/swings (deterministic)
│   │   ├── classifier/          # market classification
│   │   ├── ai/                  # Claude client, prompt builder, JSON schema/validation
│   │   ├── risk/                # deterministic risk engine (final authority)
│   │   ├── execution/           # paper/demo/live executors
│   │   ├── trade_manager/       # SL/TP movement, trailing, partials, hedging
│   │   ├── scheduler/           # APScheduler bot loop (30s/5m/15m)
│   │   ├── telegram/            # bot, commands, alerts
│   │   ├── models/              # SQLAlchemy models
│   │   ├── db/                  # session, migrations (Alembic)
│   │   └── logging/             # structured JSON logging + audit trail
│   └── tests/
├── frontend/                    # Next.js dashboard
│   ├── package.json
│   └── ...
└── nginx/
    └── nginx.conf
```
