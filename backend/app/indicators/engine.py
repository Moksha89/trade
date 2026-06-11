"""Deterministic indicator calculations over OHLCV candles.

All values are computed in Python/NumPy. The AI layer never computes indicators;
it only receives the prepared outputs of this module.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass

import numpy as np


@dataclass
class Candle:
    ts: int  # epoch seconds
    open: float
    high: float
    low: float
    close: float
    volume: float = 0.0


@dataclass
class IndicatorSet:
    price: float
    ema20: float
    ema50: float
    ema200: float
    rsi: float
    macd: float
    macd_signal: float
    macd_hist: float
    atr: float
    vwap: float | None
    support: float
    resistance: float
    swing_high: float
    swing_low: float
    volatility_pct: float  # ATR as % of price
    trend: str  # up | down | sideways

    def as_dict(self) -> dict:
        return asdict(self)


def _ema(values: np.ndarray, period: int) -> np.ndarray:
    if len(values) == 0:
        return values
    alpha = 2.0 / (period + 1.0)
    out = np.empty_like(values, dtype=float)
    out[0] = values[0]
    for i in range(1, len(values)):
        out[i] = alpha * values[i] + (1 - alpha) * out[i - 1]
    return out


def _rsi(close: np.ndarray, period: int = 14) -> float:
    if len(close) <= period:
        return 50.0
    deltas = np.diff(close)
    gains = np.where(deltas > 0, deltas, 0.0)
    losses = np.where(deltas < 0, -deltas, 0.0)
    avg_gain = gains[:period].mean()
    avg_loss = losses[:period].mean()
    for i in range(period, len(deltas)):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return float(100.0 - (100.0 / (1.0 + rs)))


def _macd(close: np.ndarray) -> tuple[float, float, float]:
    if len(close) < 35:
        return 0.0, 0.0, 0.0
    ema12 = _ema(close, 12)
    ema26 = _ema(close, 26)
    macd_line = ema12 - ema26
    signal = _ema(macd_line, 9)
    hist = macd_line - signal
    return float(macd_line[-1]), float(signal[-1]), float(hist[-1])


def _atr(high: np.ndarray, low: np.ndarray, close: np.ndarray, period: int = 14) -> float:
    n = len(close)
    if n < 2:
        return 0.0
    prev_close = close[:-1]
    tr = np.maximum.reduce(
        [
            high[1:] - low[1:],
            np.abs(high[1:] - prev_close),
            np.abs(low[1:] - prev_close),
        ]
    )
    if len(tr) < period:
        return float(tr.mean()) if len(tr) else 0.0
    return float(tr[-period:].mean())


def _vwap(high, low, close, volume) -> float | None:
    if volume is None or np.all(volume == 0):
        return None
    typical = (high + low + close) / 3.0
    total_vol = volume.sum()
    if total_vol == 0:
        return None
    return float((typical * volume).sum() / total_vol)


def _swings(high: np.ndarray, low: np.ndarray, lookback: int = 20) -> tuple[float, float]:
    window_high = high[-lookback:] if len(high) >= lookback else high
    window_low = low[-lookback:] if len(low) >= lookback else low
    return float(window_high.max()), float(window_low.min())


def _pivot_swing_levels(
    high: np.ndarray, low: np.ndarray, close: np.ndarray, price: float, atr: float,
) -> tuple[float, float]:
    """Multi-scale support/resistance from fractal swing points.

    Scans three lookback windows (short=20, medium=50, long=100 bars) for
    local swing highs/lows, then picks the nearest relevant levels above and
    below the current price. Nearby duplicate levels (within 0.5 ATR) are
    merged so the result reflects actual structure rather than noise.
    """
    n = len(high)
    if n < 5:
        return float(low.min()), float(high.max())

    # Detect fractal swing points: a bar whose high/low exceeds its 2
    # neighbours on each side.
    swing_highs: list[float] = []
    swing_lows: list[float] = []
    for lookback in (20, 50, 100):
        window = min(lookback, n)
        h = high[-window:]
        l = low[-window:]
        for i in range(2, len(h) - 2):
            if h[i] >= h[i - 1] and h[i] >= h[i - 2] and h[i] >= h[i + 1] and h[i] >= h[i + 2]:
                swing_highs.append(float(h[i]))
            if l[i] <= l[i - 1] and l[i] <= l[i - 2] and l[i] <= l[i + 1] and l[i] <= l[i + 2]:
                swing_lows.append(float(l[i]))

    # Add the overall range boundaries as fallback levels.
    swing_highs.append(float(high[-100:].max()) if n >= 100 else float(high.max()))
    swing_lows.append(float(low[-100:].min()) if n >= 100 else float(low.min()))

    # Merge levels within 0.5 ATR of each other (keep the one with more
    # touches, approximated by keeping the median of each cluster).
    merge_dist = max(atr * 0.5, price * 0.0005) if atr > 0 else price * 0.001

    def _cluster(levels: list[float]) -> list[float]:
        if not levels:
            return levels
        levels = sorted(levels)
        clusters: list[list[float]] = [[levels[0]]]
        for lv in levels[1:]:
            if lv - clusters[-1][-1] <= merge_dist:
                clusters[-1].append(lv)
            else:
                clusters.append([lv])
        return [float(np.median(c)) for c in clusters]

    res_levels = _cluster(swing_highs)
    sup_levels = _cluster(swing_lows)

    # Nearest resistance above price; nearest support below price.
    resistance = min((r for r in res_levels if r > price), default=float(high.max()))
    support = max((s for s in sup_levels if s < price), default=float(low.min()))
    return support, resistance


def compute_indicators(candles: list[Candle]) -> IndicatorSet:
    if len(candles) < 2:
        raise ValueError("need at least 2 candles to compute indicators")

    high = np.array([c.high for c in candles], dtype=float)
    low = np.array([c.low for c in candles], dtype=float)
    close = np.array([c.close for c in candles], dtype=float)
    volume = np.array([c.volume for c in candles], dtype=float)

    price = float(close[-1])
    ema20 = float(_ema(close, 20)[-1])
    ema50 = float(_ema(close, 50)[-1])
    ema200 = float(_ema(close, 200)[-1])
    rsi = _rsi(close)
    macd, macd_signal, macd_hist = _macd(close)
    atr = _atr(high, low, close)
    vwap = _vwap(high, low, close, volume)
    swing_high, swing_low = _swings(high, low)

    # Support/resistance: multi-scale fractal pivot levels.
    support, resistance = _pivot_swing_levels(high, low, close, price, atr)

    volatility_pct = (atr / price * 100.0) if price else 0.0

    # Trend: EMA stack + price location.
    if price > ema50 > ema200 and ema20 >= ema50:
        trend = "up"
    elif price < ema50 < ema200 and ema20 <= ema50:
        trend = "down"
    else:
        trend = "sideways"

    return IndicatorSet(
        price=price,
        ema20=ema20,
        ema50=ema50,
        ema200=ema200,
        rsi=rsi,
        macd=macd,
        macd_signal=macd_signal,
        macd_hist=macd_hist,
        atr=atr,
        vwap=vwap,
        support=support,
        resistance=resistance,
        swing_high=swing_high,
        swing_low=swing_low,
        volatility_pct=volatility_pct,
        trend=trend,
    )
