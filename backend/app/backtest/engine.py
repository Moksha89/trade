"""Deterministic strategy replay over historical candles.

Walks bar by bar: at each step it computes indicators on the trailing window,
classifies the market, generates a deterministic proposal, runs it through the
SAME risk engine used live, and if approved simulates the trade forward until
stop-loss or take-profit-1 is hit. Produces the spec's performance metrics.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.ai.engine import _heuristic_proposal
from app.ai.schema import Direction
from app.classifier.engine import classify_market, is_tradeable
from app.indicators.engine import Candle, compute_indicators
from app.risk.engine import RiskContext, evaluate_proposal
from app.services.settings_store import default_risk, default_strategy


@dataclass
class BacktestReport:
    instrument: str
    trades: int = 0
    wins: int = 0
    losses: int = 0
    win_rate: float = 0.0
    profit_factor: float = 0.0
    avg_r: float = 0.0
    max_drawdown_r: float = 0.0
    best_trade_r: float = 0.0
    worst_trade_r: float = 0.0
    total_r: float = 0.0
    by_strategy: dict[str, dict[str, float]] = field(default_factory=dict)
    confidence_vs_result: list[dict[str, float]] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return self.__dict__


def run_backtest(
    instrument: str,
    candles: list[Candle],
    risk: dict[str, Any] | None = None,
    strategy: dict[str, Any] | None = None,
    window: int = 200,
) -> BacktestReport:
    risk = risk or default_risk()
    strategy = strategy or default_strategy()
    report = BacktestReport(instrument=instrument)

    if len(candles) <= window + 5:
        return report

    r_results: list[float] = []
    strat_stats: dict[str, dict[str, float]] = {}
    i = window
    n = len(candles)

    while i < n:
        win = candles[i - window : i]
        try:
            ind = compute_indicators(win)
        except ValueError:
            i += 1
            continue
        condition = classify_market(ind)
        if not is_tradeable(condition):
            i += 1
            continue

        proposal = _heuristic_proposal(instrument, ind, condition)
        if not proposal.is_trade:
            i += 1
            continue

        ctx = RiskContext(account_capital=float(risk.get("account_capital", 5000)))
        decision = evaluate_proposal(proposal, ctx, risk, strategy)
        if not decision.approved:
            i += 1
            continue

        entry = proposal.entry_price
        sl = proposal.stop_loss
        tp = proposal.take_profit_1
        risk_per_unit = abs(entry - sl)
        outcome_r = None

        j = i
        while j < n:
            bar = candles[j]
            if proposal.direction == Direction.LONG:
                if bar.low <= sl:
                    outcome_r = -1.0
                    break
                if bar.high >= tp:
                    outcome_r = (tp - entry) / risk_per_unit
                    break
            else:
                if bar.high >= sl:
                    outcome_r = -1.0
                    break
                if bar.low <= tp:
                    outcome_r = (entry - tp) / risk_per_unit
                    break
            j += 1

        if outcome_r is None:
            break  # ran out of data with trade still open

        r_results.append(outcome_r)
        report.confidence_vs_result.append(
            {"confidence": proposal.confidence, "r": round(outcome_r, 2)}
        )
        s = strat_stats.setdefault(
            proposal.strategy.value, {"trades": 0, "wins": 0, "total_r": 0.0}
        )
        s["trades"] += 1
        s["wins"] += 1 if outcome_r > 0 else 0
        s["total_r"] += outcome_r

        i = j + 1  # resume after the trade closed

    if not r_results:
        return report

    wins = [r for r in r_results if r > 0]
    losses = [r for r in r_results if r <= 0]
    gross_win = sum(wins)
    gross_loss = abs(sum(losses))

    report.trades = len(r_results)
    report.wins = len(wins)
    report.losses = len(losses)
    report.win_rate = round(len(wins) / len(r_results) * 100.0, 1)
    report.profit_factor = round(gross_win / gross_loss, 2) if gross_loss else float("inf")
    report.avg_r = round(sum(r_results) / len(r_results), 3)
    report.total_r = round(sum(r_results), 2)
    report.best_trade_r = round(max(r_results), 2)
    report.worst_trade_r = round(min(r_results), 2)

    # Max drawdown over the cumulative R equity curve.
    equity = 0.0
    peak = 0.0
    max_dd = 0.0
    for r in r_results:
        equity += r
        peak = max(peak, equity)
        max_dd = max(max_dd, peak - equity)
    report.max_drawdown_r = round(max_dd, 2)

    for name, s in strat_stats.items():
        s["win_rate"] = round(s["wins"] / s["trades"] * 100.0, 1) if s["trades"] else 0.0
        s["total_r"] = round(s["total_r"], 2)
    report.by_strategy = strat_stats
    return report
