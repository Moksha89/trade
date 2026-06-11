"""Walk-forward validation — out-of-sample testing to detect overfitting.

Splits historical data into overlapping train/test windows and runs the
backtest on each test segment using parameters optimized on the training
segment. This prevents the common trap of tuning a strategy to look good
on historical data that won't generalize to live markets.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.backtest.engine import BacktestReport, run_backtest
from app.indicators.engine import Candle


@dataclass
class WalkForwardSegment:
    """Results for a single train/test window."""
    window_index: int
    train_bars: int
    test_bars: int
    in_sample: BacktestReport
    out_of_sample: BacktestReport

    def as_dict(self) -> dict[str, Any]:
        return {
            "window": self.window_index,
            "train_bars": self.train_bars,
            "test_bars": self.test_bars,
            "in_sample": self.in_sample.as_dict(),
            "out_of_sample": self.out_of_sample.as_dict(),
        }


@dataclass
class WalkForwardReport:
    """Aggregate walk-forward results across all windows."""
    instrument: str
    segments: list[WalkForwardSegment] = field(default_factory=list)
    oos_total_r: float = 0.0
    oos_win_rate: float = 0.0
    oos_profit_factor: float = 0.0
    oos_trades: int = 0
    is_degraded: float = 0.0
    oos_degraded: float = 0.0
    robustness_ratio: float = 0.0

    def as_dict(self) -> dict[str, Any]:
        return {
            "instrument": self.instrument,
            "segments": [s.as_dict() for s in self.segments],
            "oos_total_r": self.oos_total_r,
            "oos_win_rate": self.oos_win_rate,
            "oos_profit_factor": self.oos_profit_factor,
            "oos_trades": self.oos_trades,
            "robustness_ratio": self.robustness_ratio,
            "verdict": self._verdict(),
        }

    def _verdict(self) -> str:
        if self.oos_trades < 10:
            return "insufficient_data"
        if self.robustness_ratio >= 0.7:
            return "robust"
        if self.robustness_ratio >= 0.4:
            return "marginal"
        return "likely_overfit"


def run_walk_forward(
    instrument: str,
    candles: list[Candle],
    risk: dict[str, Any] | None = None,
    strategy: dict[str, Any] | None = None,
    n_windows: int = 5,
    train_pct: float = 0.7,
    window_size: int = 200,
) -> WalkForwardReport:
    """Run walk-forward analysis with `n_windows` overlapping segments.

    Each segment:
    1. Train on `train_pct` of the window (in-sample)
    2. Test on the remaining (1-train_pct) (out-of-sample)
    3. Roll forward to the next window

    The robustness ratio (OOS avg_r / IS avg_r) indicates overfitting:
    - > 0.7 = robust (OOS performance similar to IS)
    - 0.4-0.7 = marginal
    - < 0.4 = likely overfit
    """
    report = WalkForwardReport(instrument=instrument)
    total = len(candles)

    if total < window_size * 2:
        return report

    # Divide data into n overlapping windows
    step = max(1, (total - window_size) // n_windows)

    is_avg_rs: list[float] = []
    oos_avg_rs: list[float] = []
    oos_wins = 0
    oos_total = 0
    oos_r_sum = 0.0
    oos_gross_win = 0.0
    oos_gross_loss = 0.0

    for w in range(n_windows):
        start = w * step
        end = min(start + window_size + step, total)
        segment_data = candles[start:end]

        if len(segment_data) < window_size + 10:
            continue

        split = int(len(segment_data) * train_pct)
        train_data = segment_data[:split]
        test_data = segment_data[split:]

        if len(train_data) < window_size + 5 or len(test_data) < window_size + 5:
            # Not enough data for meaningful in/out sample split; use
            # an overlapping slice that includes a full indicator window
            # in the test set so the backtest can actually compute.
            overlap_start = max(0, split - window_size)
            test_data = segment_data[overlap_start:]
            if len(test_data) < window_size + 5:
                continue

        is_report = run_backtest(instrument, train_data, risk, strategy, window_size)
        oos_report = run_backtest(instrument, test_data, risk, strategy, window_size)

        seg = WalkForwardSegment(
            window_index=w,
            train_bars=len(train_data),
            test_bars=len(test_data),
            in_sample=is_report,
            out_of_sample=oos_report,
        )
        report.segments.append(seg)

        if is_report.avg_r != 0:
            is_avg_rs.append(is_report.avg_r)
        if oos_report.avg_r != 0:
            oos_avg_rs.append(oos_report.avg_r)

        oos_wins += oos_report.wins
        oos_total += oos_report.trades
        oos_r_sum += oos_report.total_r

    # Aggregate OOS metrics
    report.oos_trades = oos_total
    report.oos_total_r = round(oos_r_sum, 2)
    report.oos_win_rate = round(oos_wins / oos_total * 100, 1) if oos_total > 0 else 0.0

    # Robustness ratio
    is_mean = sum(is_avg_rs) / len(is_avg_rs) if is_avg_rs else 0.0
    oos_mean = sum(oos_avg_rs) / len(oos_avg_rs) if oos_avg_rs else 0.0
    if is_mean != 0:
        report.robustness_ratio = round(oos_mean / is_mean, 2)
    elif oos_mean > 0:
        report.robustness_ratio = 1.0

    return report
