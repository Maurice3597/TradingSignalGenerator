"""Technical indicators implemented with pandas using their standard published formulas.

References
----------
* EMA: alpha = 2 / (span + 1), seeded with the first value (pandas ``ewm(adjust=False)``).
* RSI, ATR, ADX/DMI: J. Welles Wilder Jr., *New Concepts in Technical Trading Systems* (1978).
  Wilder smoothing is an EMA with alpha = 1 / period.
* MACD: Gerald Appel; standard parameters 12/26/9.
* Stochastic oscillator: George Lane; %K(14) smoothed 3, %D = SMA(3) of %K ("slow stochastic").

All functions expect a DataFrame with ``open, high, low, close`` columns sorted oldest -> newest.
"""
from __future__ import annotations

import numpy as np
import pandas as pd


def ema(series: pd.Series, period: int) -> pd.Series:
    return series.ewm(span=period, adjust=False, min_periods=period).mean()


def wilder_smooth(series: pd.Series, period: int) -> pd.Series:
    return series.ewm(alpha=1.0 / period, adjust=False, min_periods=period).mean()


def rsi(close: pd.Series, period: int = 14) -> pd.Series:
    delta = close.diff()
    gain = delta.clip(lower=0.0)
    loss = -delta.clip(upper=0.0)
    avg_gain = wilder_smooth(gain, period)
    avg_loss = wilder_smooth(loss, period)
    rs = avg_gain / avg_loss.replace(0.0, np.nan)
    out = 100.0 - 100.0 / (1.0 + rs)
    # No losses in the window -> RSI is 100 by definition.
    out = out.where(avg_loss != 0.0, 100.0)
    return out.where(avg_gain.notna())


def macd(close: pd.Series, fast: int = 12, slow: int = 26, signal: int = 9) -> pd.DataFrame:
    macd_line = ema(close, fast) - ema(close, slow)
    signal_line = macd_line.ewm(span=signal, adjust=False, min_periods=signal).mean()
    return pd.DataFrame(
        {"macd": macd_line, "signal": signal_line, "hist": macd_line - signal_line}
    )


def stochastic(df: pd.DataFrame, k_period: int = 14, k_smooth: int = 3, d_period: int = 3) -> pd.DataFrame:
    lowest = df["low"].rolling(k_period).min()
    highest = df["high"].rolling(k_period).max()
    rng = (highest - lowest).replace(0.0, np.nan)
    fast_k = 100.0 * (df["close"] - lowest) / rng
    k = fast_k.rolling(k_smooth).mean()
    d = k.rolling(d_period).mean()
    return pd.DataFrame({"k": k, "d": d})


def true_range(df: pd.DataFrame) -> pd.Series:
    prev_close = df["close"].shift(1)
    tr = pd.concat(
        [
            df["high"] - df["low"],
            (df["high"] - prev_close).abs(),
            (df["low"] - prev_close).abs(),
        ],
        axis=1,
    ).max(axis=1)
    tr.iloc[0] = df["high"].iloc[0] - df["low"].iloc[0]
    return tr


def atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    return wilder_smooth(true_range(df), period)


def adx(df: pd.DataFrame, period: int = 14) -> pd.DataFrame:
    up_move = df["high"].diff()
    down_move = -df["low"].diff()
    plus_dm = pd.Series(
        np.where((up_move > down_move) & (up_move > 0), up_move, 0.0), index=df.index
    )
    minus_dm = pd.Series(
        np.where((down_move > up_move) & (down_move > 0), down_move, 0.0), index=df.index
    )
    atr_ = atr(df, period)
    plus_di = 100.0 * wilder_smooth(plus_dm, period) / atr_
    minus_di = 100.0 * wilder_smooth(minus_dm, period) / atr_
    di_sum = (plus_di + minus_di).replace(0.0, np.nan)
    dx = 100.0 * (plus_di - minus_di).abs() / di_sum
    adx_ = wilder_smooth(dx.dropna(), period).reindex(df.index)
    return pd.DataFrame({"adx": adx_, "plus_di": plus_di, "minus_di": minus_di})
