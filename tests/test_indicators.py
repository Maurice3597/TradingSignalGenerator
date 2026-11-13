"""Indicators are checked against independent, loop-based implementations of the textbook
formulas (Wilder 1978 for RSI/ATR/ADX; Appel for MACD; Lane for Stochastic)."""
import numpy as np
import pandas as pd
import pytest

from gold_signals import indicators as I


@pytest.fixture
def df():
    rng = np.random.default_rng(42)
    n = 300
    close = 2000 + np.cumsum(rng.normal(0, 2, n))
    open_ = np.r_[close[0], close[:-1]]
    spread = np.abs(rng.normal(0, 1.5, n))
    return pd.DataFrame({"open": open_, "high": np.maximum(open_, close) + spread,
                         "low": np.minimum(open_, close) - spread, "close": close},
                        index=pd.date_range("2025-01-01", periods=n, freq="5min", tz="UTC"))


def ref_ema(x, n):
    a, out, prev = 2 / (n + 1), [], None
    for v in x:
        prev = v if prev is None else a * v + (1 - a) * prev
        out.append(prev)
    return np.array(out)


def ref_wilder(x, n):
    out, prev = [], None
    for v in x:
        prev = v if prev is None else prev + (v - prev) / n
        out.append(prev)
    return np.array(out)


def test_ema_matches_reference(df):
    np.testing.assert_allclose(I.ema(df.close, 20).values[19:], ref_ema(df.close.values, 20)[19:])


def test_rsi_matches_wilder(df):
    c = df.close.values
    d = np.diff(c)
    g, l = ref_wilder(np.clip(d, 0, None), 14), ref_wilder(np.clip(-d, 0, None), 14)
    expected = 100 - 100 / (1 + g / l)
    # pandas series begins with NaN diff; ewm skips it so values align from index 1
    np.testing.assert_allclose(I.rsi(df.close, 14).values[14:], expected[13:], rtol=1e-9)


def test_rsi_bounds_and_extremes():
    up = pd.Series(np.arange(1.0, 60.0))
    assert I.rsi(up).iloc[-1] == 100.0
    down = pd.Series(np.arange(60.0, 1.0, -1))
    assert I.rsi(down).iloc[-1] == pytest.approx(0.0)


def test_macd_matches_reference(df):
    c = df.close.values
    line = ref_ema(c, 12) - ref_ema(c, 26)
    sig = ref_ema(line[25:], 9)
    m = I.macd(df.close)
    np.testing.assert_allclose(m["macd"].values[25:], line[25:])
    np.testing.assert_allclose(m["signal"].values[33:], sig[8:])


def test_stochastic_matches_reference(df):
    h, l, c = df.high.values, df.low.values, df.close.values
    fk = np.array([np.nan if i < 13 else 100 * (c[i] - l[i-13:i+1].min()) / (h[i-13:i+1].max() - l[i-13:i+1].min())
                   for i in range(len(c))])
    k = pd.Series(fk).rolling(3).mean().values
    st = I.stochastic(df)
    np.testing.assert_allclose(st["k"].values[20:], k[20:])
    assert st["k"].dropna().between(0, 100).all()


def test_atr_and_adx_reference(df):
    h, l, c = df.high.values, df.low.values, df.close.values
    tr = np.r_[h[0] - l[0], np.maximum.reduce([h[1:] - l[1:], abs(h[1:] - c[:-1]), abs(l[1:] - c[:-1])])]
    atr = ref_wilder(tr, 14)
    np.testing.assert_allclose(I.atr(df).values[13:], atr[13:])

    up, dn = np.r_[0, np.diff(h)], np.r_[0, -np.diff(l)]
    pdm = np.where((up > dn) & (up > 0), up, 0.0)
    mdm = np.where((dn > up) & (dn > 0), dn, 0.0)
    pdi, mdi = 100 * ref_wilder(pdm, 14) / atr, 100 * ref_wilder(mdm, 14) / atr
    a = I.adx(df)
    np.testing.assert_allclose(a["plus_di"].values[13:], pdi[13:])
    np.testing.assert_allclose(a["minus_di"].values[13:], mdi[13:])
    with np.errstate(invalid="ignore"):
        dx = 100 * abs(pdi - mdi) / (pdi + mdi)
    np.testing.assert_allclose(a["adx"].values[-1], ref_wilder(dx[13:], 14)[-1])
    assert a["adx"].dropna().between(0, 100).all()
