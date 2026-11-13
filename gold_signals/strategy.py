"""Per-timeframe signal logic and adjacent-timeframe confluence.

Strategy (evaluated on the last *closed* candle of each timeframe)
-----------------------------------------------------------------
Four directional indicators each cast a vote of +1 (bullish), -1 (bearish) or 0:

1. Trend - EMA 50 vs EMA 200: +1 if EMA50 > EMA200 and close > EMA50, -1 if the mirror holds.
2. MACD (12, 26, 9): +1 if MACD line > signal line, -1 if below.
3. RSI (14): +1 if 50 < RSI < 70 (bullish momentum, not overbought);
   -1 if 30 < RSI < 50; 0 at extremes (avoid chasing).
4. Stochastic (14, 3, 3): +1 if %K > %D and %K < 80; -1 if %K < %D and %K > 20.

A fifth indicator, ADX/DMI (14), is a trend-strength filter: a trade is only allowed if
ADX >= ADX_MIN (default 20) and the dominant DI line points the same way.

The *net* score (sum of votes, -4..+4) is used, so one opposing indicator cancels one agreeing one.
BUY  if score >= +MIN_VOTES (default 3, i.e. at least 3 bullish and none bearish) and the ADX
     filter passes in the bullish direction.
SELL if score <= -MIN_VOTES and the ADX filter passes in the bearish direction.
Otherwise NEUTRAL (stay away).

ATR (14) sizes the stop loss and take profits.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import pandas as pd

from . import indicators as ind
from .config import TIMEFRAMES, StrategyConfig

BUY, SELL, NEUTRAL = "BUY", "SELL", "NEUTRAL"


@dataclass
class TimeframeSignal:
    timeframe: str
    direction: str
    score: int
    candle_time: pd.Timestamp | None
    price: float | None = None
    votes: dict[str, int] = field(default_factory=dict)
    values: dict[str, float] = field(default_factory=dict)
    reason: str = ""
    stop_loss: float | None = None
    take_profit_1: float | None = None
    take_profit_2: float | None = None


@dataclass
class Confluence:
    lower: str
    higher: str
    direction: str

    @property
    def entry_timeframe(self) -> str:
        return self.lower


def _sign_vote(cond_up: bool, cond_down: bool) -> int:
    return 1 if cond_up else (-1 if cond_down else 0)


def min_bars_required(cfg: StrategyConfig) -> int:
    return max(cfg.ema_slow, cfg.macd_slow + cfg.macd_signal, 3 * cfg.adx_period) + 2


def analyze(df: pd.DataFrame, timeframe: str, cfg: StrategyConfig | None = None) -> TimeframeSignal:
    cfg = cfg or StrategyConfig()
    if df is None or len(df) < min_bars_required(cfg):
        n = 0 if df is None else len(df)
        return TimeframeSignal(timeframe, NEUTRAL, 0, None, reason=f"not enough data ({n} bars)")

    close = df["close"]
    ema_f = ind.ema(close, cfg.ema_fast)
    ema_s = ind.ema(close, cfg.ema_slow)
    m = ind.macd(close, cfg.macd_fast, cfg.macd_slow, cfg.macd_signal)
    r = ind.rsi(close, cfg.rsi_period)
    st = ind.stochastic(df, cfg.stoch_k, cfg.stoch_smooth, cfg.stoch_d)
    dmi = ind.adx(df, cfg.adx_period)
    a = ind.atr(df, cfg.atr_period)

    last = df.index[-1]
    price = float(close.iloc[-1])
    v = {
        "ema_fast": float(ema_f.iloc[-1]),
        "ema_slow": float(ema_s.iloc[-1]),
        "macd": float(m["macd"].iloc[-1]),
        "macd_signal": float(m["signal"].iloc[-1]),
        "rsi": float(r.iloc[-1]),
        "stoch_k": float(st["k"].iloc[-1]),
        "stoch_d": float(st["d"].iloc[-1]),
        "adx": float(dmi["adx"].iloc[-1]),
        "plus_di": float(dmi["plus_di"].iloc[-1]),
        "minus_di": float(dmi["minus_di"].iloc[-1]),
        "atr": float(a.iloc[-1]),
    }
    if any(pd.isna(x) for x in v.values()):
        return TimeframeSignal(timeframe, NEUTRAL, 0, last, price, values=v, reason="indicators not ready")

    votes = {
        "EMA": _sign_vote(
            v["ema_fast"] > v["ema_slow"] and price > v["ema_fast"],
            v["ema_fast"] < v["ema_slow"] and price < v["ema_fast"],
        ),
        "MACD": _sign_vote(v["macd"] > v["macd_signal"], v["macd"] < v["macd_signal"]),
        "RSI": _sign_vote(
            50 < v["rsi"] < cfg.rsi_overbought, cfg.rsi_oversold < v["rsi"] < 50
        ),
        "STOCH": _sign_vote(
            v["stoch_k"] > v["stoch_d"] and v["stoch_k"] < cfg.stoch_overbought,
            v["stoch_k"] < v["stoch_d"] and v["stoch_k"] > cfg.stoch_oversold,
        ),
    }
    score = sum(votes.values())

    trending = v["adx"] >= cfg.adx_min
    direction, reason = NEUTRAL, ""
    if score >= cfg.min_votes:
        if trending and v["plus_di"] > v["minus_di"]:
            direction = BUY
        else:
            reason = f"bullish votes but ADX filter failed (ADX {v['adx']:.1f})"
    elif score <= -cfg.min_votes:
        if trending and v["minus_di"] > v["plus_di"]:
            direction = SELL
        else:
            reason = f"bearish votes but ADX filter failed (ADX {v['adx']:.1f})"
    else:
        reason = f"indicators disagree (score {score:+d})"

    sig = TimeframeSignal(timeframe, direction, score, last, price, votes, v, reason)
    if direction != NEUTRAL:
        s = 1 if direction == BUY else -1
        sig.stop_loss = round(price - s * cfg.sl_atr_mult * v["atr"], 2)
        sig.take_profit_1 = round(price + s * cfg.tp1_atr_mult * v["atr"], 2)
        sig.take_profit_2 = round(price + s * cfg.tp2_atr_mult * v["atr"], 2)
        sig.reason = f"{abs(score)}/4 indicators agree, ADX {v['adx']:.1f}"
    return sig


def find_confluences(signals: dict[str, TimeframeSignal]) -> list[Confluence]:
    """Adjacent timeframe pairs (5m/15m, 15m/30m, 30m/1h) that share the same BUY/SELL signal.
    Entry is recommended on the lower timeframe of each pair."""
    out = []
    for lower, higher in zip(TIMEFRAMES, TIMEFRAMES[1:]):
        a, b = signals.get(lower), signals.get(higher)
        if a and b and a.direction == b.direction and a.direction != NEUTRAL:
            out.append(Confluence(lower, higher, a.direction))
    return out
