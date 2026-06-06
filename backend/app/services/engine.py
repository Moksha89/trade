"""Trading orchestration engine.

Ties the layers together for the 24/7 loop:
  market data → indicators → classifier → AI proposal → risk engine →
  (approval gate) → execution → live trade management.

All functions take a DB session so they can run from the scheduler worker or be
triggered manually from the API. Designed for paper mode out of the box; demo/
live execution is used automatically when Capital.com credentials are present.
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.ai.engine import propose_trade
from app.ai.schema import Direction, EntryType, ManagementPlan, Strategy, TradeProposal
from app.classifier.engine import classify_market, is_tradeable
from app.config import settings
from app.execution.factory import get_executor
from app.indicators.engine import compute_indicators
from app.market_data.base import MarketDataProvider
from app.market_data.factory import get_provider
from app.models import Trade, TradeIdea
from app.risk.engine import RiskContext, evaluate_proposal
from app.services import accounts
from app.services.audit import log_event
from app.services.settings_store import (
    AI,
    BROKER_RUNTIME,
    RISK,
    STRATEGY,
    get_bot_state,
    get_group,
    update_group,
)
from app.telegram.notifier import notify
from app.trade_manager.engine import compute_management_actions


# A market order can fill away from the price we sized against. We accept a
# small overshoot (slippage is unavoidable), but if the actual fill pushes
# per-trade risk above cap × this factor we abort the position instead of
# letting it ride oversized.
SLIPPAGE_ABORT_TOLERANCE = 1.3


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _risk_unit_multiplier(provider: MarketDataProvider, instrument: str) -> float:
    """Account-currency value of a 1-point move (size 1) for `instrument`.

    Returns 0.0 if it cannot be determined; the risk engine treats a
    non-positive multiplier as "cannot size" and rejects, so an order is never
    placed with an unverified currency conversion.
    """
    try:
        return float(provider.risk_unit_multiplier(instrument))
    except Exception:  # noqa: BLE001
        return 0.0


# --------------------------------------------------------------------------
# Scan & propose (every 5 minutes)
# --------------------------------------------------------------------------
def run_scan(db: Session) -> list[TradeIdea]:
    risk = get_group(db, RISK)
    strategy = get_group(db, STRATEGY)
    ai_cfg = get_group(db, AI)
    state = get_bot_state(db)
    provider = get_provider()

    created: list[TradeIdea] = []

    if state.trading_locked:
        log_event(db, "scan_skipped", {"reason": "trading locked"})
        return created

    open_now = accounts.open_trades(db)
    max_active = int(risk.get("max_active_trades", 2))
    if len(open_now) >= max_active:
        log_event(db, "scan_skipped", {"reason": "max active trades"})
        return created

    acct_stats = accounts.stats(db)
    open_risk = accounts.current_open_risk(db)

    for instrument in risk.get("allowed_instruments", []):
        if any(t.instrument == instrument for t in open_now):
            continue
        try:
            candles = provider.get_candles(instrument, "5M", 250)
            ind = compute_indicators(candles)
            snap = provider.get_snapshot(instrument)
        except Exception as exc:  # noqa: BLE001
            log_event(db, "market_data_error", {"error": str(exc)}, instrument=instrument)
            continue

        spread_too_high = snap.quote.spread_points > float(risk.get("max_spread_points", 5))
        condition = classify_market(
            ind, news_risk=False, spread_too_high=spread_too_high
        )
        log_event(
            db,
            "market_scan",
            {
                "classification": condition.value,
                "price": ind.price,
                "trend": ind.trend,
                "rsi": round(ind.rsi, 1),
                "spread": snap.quote.spread_points,
            },
            instrument=instrument,
        )

        if not is_tradeable(condition):
            continue
        if not ai_cfg.get("allow_create_ideas", True):
            continue

        proposal, payload, phash = propose_trade(
            instrument,
            ind,
            condition,
            context={
                "client_sentiment": snap.client_sentiment_long_pct,
                "news_risk": False,
                "open_positions": [t.instrument for t in open_now],
                "existing_exposure_aed": open_risk,
                "available_risk_budget_aed": float(risk.get("max_combined_open_risk", 100))
                - open_risk,
            },
            model=ai_cfg.get("model"),
        )
        # The AI must analyse the scanned instrument; never trust an echoed value.
        proposal.instrument = instrument
        log_event(
            db,
            "ai_response",
            {
                "prompt_hash": phash,
                "direction": proposal.direction.value,
                "strategy": proposal.strategy.value,
                "confidence": proposal.confidence,
            },
            instrument=instrument,
        )

        ctx = RiskContext(
            account_capital=float(risk.get("account_capital", settings.account_start_capital)),
            open_trades=[{"instrument": t.instrument, "direction": t.direction} for t in open_now],
            current_open_risk_aed=open_risk,
            trades_today=int(acct_stats["trades_today"]),
            losses_today=int(acct_stats["losses_today"]),
            realized_pl_today=float(acct_stats["realized_pl_today"]),
            realized_pl_week=float(acct_stats["realized_pl_week"]),
            spread_points=snap.quote.spread_points,
            news_risk=False,
            market_open=snap.market_open,
            trading_locked=state.trading_locked,
            account_ccy_per_point=_risk_unit_multiplier(provider, instrument),
        )
        decision = evaluate_proposal(proposal, ctx, risk, strategy)

        idea = TradeIdea(
            instrument=instrument,
            direction=proposal.direction.value,
            strategy=proposal.strategy.value,
            entry_type=proposal.entry_type.value,
            entry_price=proposal.entry_price,
            stop_loss=proposal.stop_loss,
            take_profit_1=proposal.take_profit_1,
            take_profit_2=proposal.take_profit_2,
            confidence=proposal.confidence,
            risk_reward=proposal.risk_reward,
            position_size=decision.computed_size,
            risk_aed=decision.computed_risk_aed,
            rationale=proposal.rationale,
            invalidation_condition=proposal.invalidation_condition,
            risk_flags=proposal.risk_flags,
            management_plan=proposal.management_plan.model_dump(),
            market_classification=condition.value,
            risk_approved=decision.approved,
            risk_reason=decision.reason,
            ai_prompt_hash=phash,
            status="approved" if decision.approved else "rejected",
        )
        db.add(idea)
        db.flush()
        created.append(idea)

        log_event(
            db,
            "risk_decision",
            {"approved": decision.approved, "reason": decision.reason, "idea_id": idea.id},
            instrument=instrument,
        )
        state.last_ai_decision = f"{instrument} {proposal.direction.value} ({proposal.strategy.value})"
        if not decision.approved:
            state.last_risk_rejection = f"{instrument}: {decision.reason}"

        if decision.approved:
            notify(
                f"💡 Trade idea: {instrument} {proposal.direction.value.upper()} "
                f"{proposal.strategy.value} | entry {proposal.entry_price} SL {proposal.stop_loss} "
                f"TP {proposal.take_profit_1} | conf {proposal.confidence:.0f}% RR {proposal.risk_reward}"
            )
            # Auto-execute only if explicitly enabled (off by default).
            if (
                ai_cfg.get("auto_mode")
                and ai_cfg.get("allow_open_trades")
                and state.auto_trading_enabled
                and settings.execution_mode in ("demo", "live", "paper")
            ):
                execute_idea(db, idea)

    db.commit()
    return created


# --------------------------------------------------------------------------
# Execute an approved idea
# --------------------------------------------------------------------------
def _proposal_from_idea(idea: TradeIdea, entry_price: float | None = None) -> TradeProposal:
    return TradeProposal(
        instrument=idea.instrument,
        direction=Direction(idea.direction),
        strategy=Strategy(idea.strategy),
        entry_type=EntryType(idea.entry_type),
        entry_price=idea.entry_price if entry_price is None else entry_price,
        stop_loss=idea.stop_loss,
        take_profit_1=idea.take_profit_1,
        take_profit_2=idea.take_profit_2,
        confidence=idea.confidence,
        risk_reward=idea.risk_reward,
        management_plan=ManagementPlan(**(idea.management_plan or {})),
    )


def _execution_entry_and_spread(
    provider: MarketDataProvider, idea: TradeIdea
) -> tuple[float, float]:
    """Live execution price and spread for sizing/gating at execution time.

    The AI's proposed entry is stale by the time we execute; on wide-spread
    instruments (crypto) the real fill can be far from it, which would inflate
    the stop distance and blow past the per-trade risk cap. For market entries
    we size against the live executable price (ask for longs, bid for shorts),
    so realized risk reflects the actual fill. Limit/stop entries fill at their
    own level, so we keep the proposed price. The spread (ask-bid) is returned
    so the risk engine can reject setups whose stop is too tight versus it.
    Falls back to the proposed entry (and zero spread) if no live quote.
    """
    try:
        q = provider.get_quote(idea.instrument)
    except Exception:  # noqa: BLE001
        return idea.entry_price, 0.0
    spread = q.spread_points or abs((q.ask or 0.0) - (q.bid or 0.0))
    if idea.entry_type != EntryType.MARKET.value:
        return idea.entry_price, spread
    if idea.direction == Direction.LONG.value:
        return (q.ask or idea.entry_price), spread
    return (q.bid or idea.entry_price), spread


def execute_idea(db: Session, idea: TradeIdea) -> Trade | None:
    if not idea.risk_approved:
        log_event(db, "execute_blocked", {"idea_id": idea.id, "reason": idea.risk_reason})
        return None
    if idea.status == "executed":
        return None

    # Re-validate against the CURRENT state — conditions may have changed since the
    # proposal was made (other trades opened, losses hit limits, etc.). The risk
    # engine is the final authority at execution time, not just at proposal time.
    risk = get_group(db, RISK)
    strategy = get_group(db, STRATEGY)
    state = get_bot_state(db)
    open_now = accounts.open_trades(db)
    acct_stats = accounts.stats(db)
    provider = get_provider()
    ctx = RiskContext(
        account_capital=float(risk.get("account_capital", settings.account_start_capital)),
        open_trades=[{"instrument": t.instrument, "direction": t.direction} for t in open_now],
        current_open_risk_aed=accounts.current_open_risk(db),
        trades_today=int(acct_stats["trades_today"]),
        losses_today=int(acct_stats["losses_today"]),
        realized_pl_today=float(acct_stats["realized_pl_today"]),
        realized_pl_week=float(acct_stats["realized_pl_week"]),
        market_open=True,
        trading_locked=state.trading_locked,
        account_ccy_per_point=_risk_unit_multiplier(provider, idea.instrument),
    )
    # Size against the live executable price so wide-spread fills can't blow
    # past the per-trade risk cap (the proposed entry is stale by now). The
    # live spread feeds the stop-vs-spread quality gate in the risk engine.
    exec_entry, exec_spread = _execution_entry_and_spread(provider, idea)
    ctx.spread_points = exec_spread
    recheck = evaluate_proposal(_proposal_from_idea(idea, exec_entry), ctx, risk, strategy)
    if not recheck.approved:
        idea.status = "rejected"
        idea.risk_reason = f"blocked at execution: {recheck.reason}"
        log_event(db, "execute_blocked", {"idea_id": idea.id, "reason": recheck.reason},
                  instrument=idea.instrument)
        db.commit()
        return None
    # Use the freshly computed authoritative size.
    idea.position_size = recheck.computed_size
    idea.risk_aed = recheck.computed_risk_aed

    executor = get_executor()
    direction = idea.direction
    result = executor.open(
        instrument=idea.instrument,
        direction=direction,
        size=idea.position_size,
        entry_price=exec_entry,
        stop_loss=idea.stop_loss,
        take_profit=idea.take_profit_1,
    )
    if not result.ok:
        idea.status = "rejected"
        idea.risk_reason = f"execution failed: {result.error}"
        log_event(db, "order_failed", {"idea_id": idea.id, "error": result.error},
                  instrument=idea.instrument)
        notify(f"⚠️ Order failed for {idea.instrument}: {result.error}")
        db.commit()
        return None

    # Re-derive risk from the ACTUAL fill (not the pre-fill quote we sized
    # against). A market order can fill away from the quote; with a fixed stop
    # that changes the real risk. If the slip pushed risk well past the
    # per-trade cap, abort the position rather than let it ride oversized.
    fill = result.fill_price or exec_entry
    fx = ctx.account_ccy_per_point  # AED per 1-point move, size 1 (>0 here)
    risk_per_unit = abs(fill - idea.stop_loss)
    actual_risk_aed = idea.position_size * risk_per_unit * fx
    cap = float(risk.get("max_risk_per_trade", 50))
    if actual_risk_aed > cap * SLIPPAGE_ABORT_TOLERANCE and result.deal_id:
        closed = executor.close(result.deal_id, fill)
        idea.risk_reason = (
            f"aborted: fill slip pushed risk to {actual_risk_aed:.0f} AED "
            f"(> {cap * SLIPPAGE_ABORT_TOLERANCE:.0f} cap+tol)"
        )
        common = dict(
            idea_id=idea.id, mode=executor.mode, instrument=idea.instrument,
            direction=direction, strategy=idea.strategy, entry_price=fill,
            size=idea.position_size, stop_loss=idea.stop_loss,
            take_profit_1=idea.take_profit_1, take_profit_2=idea.take_profit_2,
            initial_risk_aed=round(actual_risk_aed, 2),
            initial_risk_per_unit=risk_per_unit,
            management_plan=idea.management_plan,
            deal_reference=result.deal_reference, deal_id=result.deal_id,
        )
        if closed and closed.ok:
            # Closed cleanly. Book the real round-trip cost (the spread/slippage
            # paid to open then immediately close) as a closed trade so the
            # daily-loss governor and loss-streak guard actually see it —
            # otherwise these aborts bleed the account invisibly past the caps.
            close_fill = closed.fill_price or fill
            sign = 1.0 if direction == Direction.LONG.value else -1.0
            realized = round((close_fill - fill) * sign * idea.position_size * fx, 2)
            trade = Trade(**common, current_price=close_fill, status="closed",
                          realized_pl=realized, unrealized_pl=0.0,
                          closed_at=_utcnow(), close_reason="aborted_slippage")
            db.add(trade)
            idea.status = "rejected"
            log_event(
                db, "order_aborted_slippage",
                {"idea_id": idea.id, "deal_id": result.deal_id, "fill": fill,
                 "close_fill": close_fill, "risk_aed": round(actual_risk_aed, 2),
                 "realized_pl": realized, "close_ok": True},
                instrument=idea.instrument,
            )
            notify(f"⛔ {idea.instrument} opened then closed: fill slipped, risk "
                   f"{actual_risk_aed:.0f} AED over the {cap:.0f} cap | cost {realized:+.2f} AED")
            db.commit()
            return None
        # Close FAILED — the oversized position is still live at the broker. Track
        # it as an open trade so the manage/reconcile loop handles it instead of
        # leaving an untracked position bleeding off-book. Its server-side stop
        # still bounds the downside.
        trade = Trade(**common, current_price=fill, status="open")
        db.add(trade)
        idea.status = "executed"
        log_event(
            db, "order_aborted_slippage",
            {"idea_id": idea.id, "deal_id": result.deal_id, "fill": fill,
             "risk_aed": round(actual_risk_aed, 2), "close_ok": False},
            instrument=idea.instrument,
        )
        notify(f"⛔ {idea.instrument} slipped (risk {actual_risk_aed:.0f} AED) and the "
               f"abort-close FAILED: {closed.error if closed else 'no result'} — now "
               f"tracked as OPEN for the manage loop to close")
        db.commit()
        return trade
    trade = Trade(
        idea_id=idea.id,
        mode=executor.mode,
        instrument=idea.instrument,
        direction=direction,
        strategy=idea.strategy,
        entry_price=fill,
        size=idea.position_size,
        stop_loss=idea.stop_loss,
        take_profit_1=idea.take_profit_1,
        take_profit_2=idea.take_profit_2,
        initial_risk_aed=round(actual_risk_aed, 2),
        initial_risk_per_unit=risk_per_unit,
        current_price=fill,
        management_plan=idea.management_plan,
        deal_reference=result.deal_reference,
        deal_id=result.deal_id,
        status="open",
    )
    db.add(trade)
    idea.status = "executed"
    db.flush()
    log_event(
        db,
        "order_created",
        {"trade_id": trade.id, "deal_id": trade.deal_id, "mode": trade.mode,
         "size": trade.size, "entry": trade.entry_price},
        instrument=trade.instrument,
    )
    notify(
        f"✅ Opened {trade.instrument} {direction.upper()} size {trade.size:.4f} "
        f"@ {trade.entry_price} | SL {trade.stop_loss} TP {trade.take_profit_1} ({trade.mode})"
    )
    db.commit()
    return trade


# --------------------------------------------------------------------------
# Manage open trades (every 30s–1m)
# --------------------------------------------------------------------------
def _ccy_mult(trade: Trade) -> float:
    """Account-currency value of a 1-point move for this trade.

    Derived from the sizing recorded at open (risk_aed = size * risk_per_unit *
    mult), so P/L is reported in the account currency (AED) rather than the
    instrument's quote currency. Falls back to 1.0 when it can't be derived.
    """
    denom = trade.size * (trade.initial_risk_per_unit or 0.0)
    if denom > 0 and trade.initial_risk_aed:
        return trade.initial_risk_aed / denom
    return 1.0


def _mark_to_market(trade: Trade, price: float) -> None:
    trade.current_price = price
    mult = _ccy_mult(trade)
    if trade.direction == "long":
        trade.unrealized_pl = round(trade.size * (price - trade.entry_price) * mult, 2)
    else:
        trade.unrealized_pl = round(trade.size * (trade.entry_price - price) * mult, 2)


def close_trade(db: Session, trade: Trade, price: float, reason: str) -> None:
    # Send the real close first so we can book P/L at the actual fill and alert
    # if the broker rejects it (e.g. the position was already closed).
    executor = get_executor()
    res = executor.close(trade.deal_id, price)
    fill = res.fill_price if (res and res.ok and res.fill_price) else price
    if res and not res.ok:
        log_event(db, "close_failed", {"trade_id": trade.id, "error": res.error},
                  instrument=trade.instrument)
        notify(f"⚠️ {trade.instrument} close may have failed: {res.error}")
    mult = _ccy_mult(trade)
    if trade.direction == "long":
        pl = trade.size * (fill - trade.entry_price) * mult
    else:
        pl = trade.size * (trade.entry_price - fill) * mult
    trade.realized_pl = round(trade.realized_pl + pl, 2)
    trade.unrealized_pl = 0.0
    trade.current_price = fill
    trade.status = "closed"
    trade.closed_at = _utcnow()
    trade.close_reason = reason
    log_event(
        db,
        "trade_closed",
        {"trade_id": trade.id, "reason": reason, "realized_pl": trade.realized_pl},
        instrument=trade.instrument,
    )
    notify(f"🔚 Closed {trade.instrument} ({reason}) | P/L {trade.realized_pl:+.2f}")


def _partial_close(db: Session, trade: Trade, price: float, pct: float) -> None:
    closed_size = trade.size * pct / 100.0
    mult = _ccy_mult(trade)
    if trade.direction == "long":
        pl = closed_size * (price - trade.entry_price) * mult
    else:
        pl = closed_size * (trade.entry_price - price) * mult
    trade.realized_pl = round(trade.realized_pl + pl, 2)
    trade.size = round(trade.size - closed_size, 6)
    trade.partial_closed = True
    log_event(
        db,
        "partial_close",
        {"trade_id": trade.id, "pct": pct, "realized_pl": trade.realized_pl},
        instrument=trade.instrument,
    )
    notify(f"📉 Partial {pct:.0f}% close {trade.instrument} | realized {trade.realized_pl:+.2f}")


def _reconcile_closed_on_broker(db: Session, trade: Trade) -> None:
    """A live position is no longer held at the broker (its server-side stop or
    take-profit fired, or it was closed manually in the app). Mirror that into
    our record so the slot frees up and P/L is booked. Uses the last
    mark-to-market (already in account currency) as the realized figure."""
    trade.realized_pl = round((trade.realized_pl or 0.0) + (trade.unrealized_pl or 0.0), 2)
    trade.unrealized_pl = 0.0
    trade.status = "closed"
    trade.closed_at = _utcnow()
    trade.close_reason = "closed_on_broker"
    log_event(
        db, "trade_reconciled",
        {"trade_id": trade.id, "deal_id": trade.deal_id, "realized_pl": trade.realized_pl},
        instrument=trade.instrument,
    )
    notify(f"ℹ️ {trade.instrument} closed on broker (stop/target/manual) | P/L {trade.realized_pl:+.2f} AED")


def _instrument_from_epic(epic: str) -> str:
    """Map a broker epic to our instrument name.

    The live system keys instruments by their epic (e.g. BTCUSD, GOLD, US100 —
    see the allowed-instruments list), so the epic IS the instrument name.
    Returning it keeps adopted trades named consistently with bot trades."""
    return epic or ""


def _adopt_untracked_positions(
    db: Session, provider: MarketDataProvider, executor, broker_deals: dict
) -> None:
    """Pull any live broker position we don't already track into the DB.

    This makes manually-placed trades (opened in the Capital.com app) visible on
    the dashboard, counted toward the risk caps, and managed by the bot exactly
    like a bot-opened trade. Without this, manual positions are off-book: hidden
    from the panel and invisible to the combined-risk / max-active governors.
    """
    known = {t.deal_id for t in accounts.open_trades(db) if t.deal_id}
    for did, row in broker_deals.items():
        if did in known:
            continue
        pos = row.get("position") or {}
        mkt = row.get("market") or {}
        instrument = _instrument_from_epic(mkt.get("epic") or "")
        entry = float(pos.get("level") or 0.0)
        size = float(pos.get("size") or 0.0)
        if entry <= 0 or size <= 0:
            continue
        direction = "long" if str(pos.get("direction", "")).upper() == "BUY" else "short"
        stop = pos.get("stopLevel")
        # Capital's position node carries the take-profit under "profitLevel"
        # (NOT "limitLevel", which is for working orders).
        tp = pos.get("profitLevel")
        rpu = abs(entry - float(stop)) if stop else 0.0
        mult = _risk_unit_multiplier(provider, instrument)
        risk_aed = round(size * rpu * mult, 2) if (rpu > 0 and mult > 0) else 0.0
        trade = Trade(
            idea_id=None,
            mode=executor.mode,
            instrument=instrument,
            direction=direction,
            strategy="manual",
            entry_price=entry,
            size=size,
            stop_loss=float(stop) if stop else entry,
            take_profit_1=float(tp) if tp else 0.0,
            take_profit_2=0.0,
            initial_risk_aed=risk_aed,
            initial_risk_per_unit=rpu,
            current_price=entry,
            status="open",
            management_plan=ManagementPlan().model_dump(),
            deal_id=str(did),
            deal_reference=pos.get("dealReference"),
        )
        db.add(trade)
        db.flush()
        log_event(
            db, "trade_adopted",
            {"trade_id": trade.id, "deal_id": str(did), "instrument": instrument,
             "direction": direction, "size": size, "entry": entry,
             "stop": stop, "tp": tp, "risk_aed": risk_aed, "has_stop": stop is not None},
            instrument=instrument,
        )
        notify(
            f"📥 Adopted untracked {instrument} {direction.upper()} size {size:.4f} "
            f"@ {entry} (SL {stop}, TP {tp}) — now tracked & managed"
        )
    db.commit()


def manage_open_trades(db: Session) -> None:
    provider = get_provider()
    executor = get_executor()
    live = executor.mode in ("demo", "live")

    # In live/demo, learn which positions the broker still holds so we can
    # detect server-side closes (stop/target) and never act on a phantom trade.
    broker_deals: dict | None = None
    if live and hasattr(executor, "client"):
        try:
            broker_deals = {}
            for p in executor.client.get_positions():
                did = (p.get("position") or {}).get("dealId")
                if did:
                    broker_deals[did] = p
        except Exception as exc:  # noqa: BLE001
            log_event(db, "reconcile_error", {"error": str(exc)})
            broker_deals = None  # unknown this tick → leave everything untouched

    # Adopt any broker position we don't track yet (e.g. trades placed manually
    # in the Capital.com app) so every position in the account is on-book.
    if live and broker_deals:
        _adopt_untracked_positions(db, provider, executor, broker_deals)

    for trade in accounts.open_trades(db):
        # Reconcile first: if the broker no longer holds this deal, it closed
        # server-side or manually — mirror it and free the slot.
        if live and broker_deals is not None and trade.deal_id and trade.deal_id not in broker_deals:
            _reconcile_closed_on_broker(db, trade)
            db.commit()
            continue

        try:
            quote = provider.get_quote(trade.instrument)
        except Exception as exc:  # noqa: BLE001
            log_event(db, "market_data_error", {"error": str(exc)}, instrument=trade.instrument)
            continue
        # Mark to the price the position would actually close at (cross the
        # spread): bid for longs, ask for shorts. This matches the broker's
        # unrealized P/L instead of the optimistic mid.
        price = quote.bid if trade.direction == "long" else quote.ask
        _mark_to_market(trade, price)

        # Synthetic stop/target close is paper-only; live positions are closed by
        # the broker's own server-side stop/TP and mirrored via reconciliation.
        if not live:
            if trade.direction == "long":
                if price <= trade.stop_loss:
                    close_trade(db, trade, trade.stop_loss, "stop_loss"); db.commit(); continue
                if trade.take_profit_2 and price >= trade.take_profit_2:
                    close_trade(db, trade, trade.take_profit_2, "take_profit_2"); db.commit(); continue
            else:
                if price >= trade.stop_loss:
                    close_trade(db, trade, trade.stop_loss, "stop_loss"); db.commit(); continue
                if trade.take_profit_2 and price <= trade.take_profit_2:
                    close_trade(db, trade, trade.take_profit_2, "take_profit_2"); db.commit(); continue

        plan = trade.management_plan or {}
        trail_level = None
        if trade.partial_closed:
            try:
                ind = compute_indicators(provider.get_candles(trade.instrument, "5M", 60))
                if trade.direction == "long":
                    trail_level = ind.price - ind.atr
                else:
                    trail_level = ind.price + ind.atr
            except Exception:  # noqa: BLE001
                trail_level = None

        actions = compute_management_actions(
            direction=trade.direction,
            entry=trade.entry_price,
            current_price=price,
            risk_per_unit=trade.initial_risk_per_unit,
            current_sl=trade.stop_loss,
            plan=plan,
            breakeven_done=trade.breakeven_moved,
            profit_locked=trade.profit_locked,
            partial_done=trade.partial_closed,
            trail_level=trail_level,
        )

        # TP1 reached without an explicit +2R partial → take the partial there too.
        tp1_hit = trade.take_profit_1 and (
            (trade.direction == "long" and price >= trade.take_profit_1)
            or (trade.direction == "short" and price <= trade.take_profit_1)
        )
        if actions.close:
            close_trade(db, trade, price, ";".join(actions.reasons) or "managed_close")
            db.commit()
            continue

        if actions.new_stop_loss is not None:
            old = trade.stop_loss
            # Push to the broker FIRST; only record the move if it actually
            # took, so our stored stop can never drift from the broker's. Spec:
            # alert immediately if a stop/target modification fails.
            res = executor.modify(trade.deal_id, actions.new_stop_loss, None)
            if res.ok:
                trade.stop_loss = actions.new_stop_loss
                trade.last_sltp_update = _utcnow()
                if any("breakeven" in r for r in actions.reasons):
                    trade.breakeven_moved = True
                if any("lock" in r for r in actions.reasons):
                    trade.profit_locked = True
                    trade.breakeven_moved = True
                log_event(
                    db, "sltp_moved",
                    {"trade_id": trade.id, "old_sl": old, "new_sl": trade.stop_loss,
                     "reasons": actions.reasons},
                    instrument=trade.instrument,
                )
                notify(f"🔧 {trade.instrument} SL → {trade.stop_loss} ({','.join(actions.reasons)})")
            else:
                log_event(
                    db, "sltp_move_failed",
                    {"trade_id": trade.id, "intended_sl": actions.new_stop_loss, "error": res.error},
                    instrument=trade.instrument,
                )
                notify(f"⚠️ {trade.instrument} SL move FAILED ({res.error}); broker stop stays at {old}")

        # Partial close is paper-only. In live the server-side take-profit closes
        # the full position at TP1, so a DB-only partial would desync from the
        # broker; we deliberately skip it.
        if not live and (actions.partial_close_percent or tp1_hit) and not trade.partial_closed:
            pct = actions.partial_close_percent or float(plan.get("partial_close_percent", 50))
            _partial_close(db, trade, price, pct)

        db.commit()


# --------------------------------------------------------------------------
# Health + journal
# --------------------------------------------------------------------------
def run_health(db: Session) -> None:
    state = get_bot_state(db)
    state.last_heartbeat = _utcnow()
    # Read-only broker connectivity. We authenticate and read the balance
    # whenever credentials are configured, regardless of execution mode, so the
    # dashboard can show a live connection without ever placing an order
    # (order routing is gated separately by EXECUTION_MODE in the executor).
    connected = False
    from app.broker.capital import get_capital_client

    client = get_capital_client()
    if client.configured:
        try:
            client.ensure_session()
            connected = True
            try:
                bal = client.get_account_balance()
                update_group(
                    db,
                    BROKER_RUNTIME,
                    {
                        "connected": True,
                        "environment": settings.capital_environment,
                        "balance": bal.get("balance"),
                        "available": bal.get("available"),
                        "synced_at": _utcnow().isoformat(),
                    },
                )
            except Exception:  # noqa: BLE001
                pass
        except Exception:  # noqa: BLE001
            connected = False
    if not connected:
        update_group(db, BROKER_RUNTIME, {"connected": False})
    state.broker_connected = connected
    db.commit()


def save_journal(db: Session) -> None:
    from app.models import JournalSnapshot

    s = accounts.stats(db)
    snapshot = {
        "ts": _utcnow().isoformat(),
        **s,
        "current_open_risk": accounts.current_open_risk(db),
        "open_trades": [
            {
                "instrument": t.instrument,
                "direction": t.direction,
                "entry": t.entry_price,
                "sl": t.stop_loss,
                "unrealized_pl": t.unrealized_pl,
            }
            for t in accounts.open_trades(db)
        ],
    }
    db.add(JournalSnapshot(snapshot=snapshot))
    log_event(db, "journal_snapshot", {"open_trades": s["open_trades_count"]})
    db.commit()
