"""OHLC market-data providers for gold.

* Twelve Data (``XAU/USD`` spot) - https://twelvedata.com/docs#time-series
  GET https://api.twelvedata.com/time_series?symbol=XAU/USD&interval=5min&outputsize=500&timezone=UTC&apikey=KEY
* Yahoo Finance chart endpoint (``GC=F`` COMEX gold futures, unofficial, no key) - fallback only.

Only *closed* candles are returned: the bar that is still forming is dropped so signals never
repaint.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

import pandas as pd
import requests

from .config import TIMEFRAME_MINUTES, AppConfig

log = logging.getLogger(__name__)

TWELVEDATA_URL = "https://api.twelvedata.com/time_series"
TWELVEDATA_INTERVALS = {"5m": "5min", "15m": "15min", "30m": "30min", "1h": "1h"}

YAHOO_URL = "https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
YAHOO_INTERVALS = {"5m": ("5m", "5d"), "15m": ("15m", "20d"), "30m": ("30m", "40d"), "1h": ("60m", "60d")}


class DataError(RuntimeError):
    pass


def drop_unclosed(df: pd.DataFrame, timeframe: str, now: datetime | None = None) -> pd.DataFrame:
    """Remove bars whose period has not finished yet (index = bar open time, UTC)."""
    now = now or datetime.now(timezone.utc)
    close_times = df.index + pd.Timedelta(minutes=TIMEFRAME_MINUTES[timeframe])
    return df[close_times <= pd.Timestamp(now)]


class TwelveDataProvider:
    def __init__(self, api_key: str, symbol: str = "XAU/USD", session: requests.Session | None = None):
        if not api_key:
            raise DataError("TWELVEDATA_API_KEY is not set")
        self.api_key = api_key
        self.symbol = symbol
        self.session = session or requests.Session()

    def fetch(self, timeframe: str, bars: int = 500) -> pd.DataFrame:
        params = {
            "symbol": self.symbol,
            "interval": TWELVEDATA_INTERVALS[timeframe],
            "outputsize": bars,
            "timezone": "UTC",
            "order": "ASC",
            "apikey": self.api_key,
        }
        resp = self.session.get(TWELVEDATA_URL, params=params, timeout=20)
        resp.raise_for_status()
        payload = resp.json()
        if payload.get("status") == "error" or "values" not in payload:
            raise DataError(f"Twelve Data error: {payload.get('code')} {payload.get('message')}")
        df = pd.DataFrame(payload["values"])
        df["datetime"] = pd.to_datetime(df["datetime"], utc=True)
        df = df.set_index("datetime").sort_index()
        df = df[["open", "high", "low", "close"]].astype(float)
        return drop_unclosed(df, timeframe)


class YahooProvider:
    def __init__(self, symbol: str = "GC=F", session: requests.Session | None = None):
        self.symbol = symbol
        self.session = session or requests.Session()

    def fetch(self, timeframe: str, bars: int = 500) -> pd.DataFrame:
        interval, rng = YAHOO_INTERVALS[timeframe]
        resp = self.session.get(
            YAHOO_URL.format(symbol=self.symbol),
            params={"interval": interval, "range": rng},
            headers={"User-Agent": "Mozilla/5.0"},
            timeout=20,
        )
        resp.raise_for_status()
        chart = resp.json().get("chart", {})
        if chart.get("error") or not chart.get("result"):
            raise DataError(f"Yahoo error: {chart.get('error')}")
        result = chart["result"][0]
        quote = result["indicators"]["quote"][0]
        df = pd.DataFrame(
            {k: quote[k] for k in ("open", "high", "low", "close")},
            index=pd.to_datetime(result["timestamp"], unit="s", utc=True),
        ).dropna()
        df.index.name = "datetime"
        df = df[~df.index.duplicated(keep="last")].sort_index()
        return drop_unclosed(df, timeframe).tail(bars)


def make_provider(cfg: AppConfig):
    if cfg.data_provider == "twelvedata":
        return TwelveDataProvider(cfg.twelvedata_api_key, cfg.twelvedata_symbol)
    if cfg.data_provider == "yahoo":
        return YahooProvider(cfg.yahoo_symbol)
    raise DataError(f"Unknown DATA_PROVIDER: {cfg.data_provider}")


class CachedFetcher:
    """Re-downloads a timeframe only after a new candle of that timeframe has closed.

    This keeps API usage low: per day ~288 (5m) + 96 (15m) + 48 (30m) + 24 (1h) = 456 requests,
    inside the Twelve Data free plan limit of 800/day and 8/minute.
    """

    def __init__(self, provider, bars: int = 500):
        self.provider = provider
        self.bars = bars
        self._cache: dict[str, pd.DataFrame] = {}

    def get(self, timeframe: str, now: datetime | None = None) -> pd.DataFrame:
        now = now or datetime.now(timezone.utc)
        cached = self._cache.get(timeframe)
        if cached is not None and not cached.empty:
            next_close = cached.index[-1] + timedelta(minutes=2 * TIMEFRAME_MINUTES[timeframe])
            if pd.Timestamp(now) < next_close:
                return cached
        df = self.provider.fetch(timeframe, self.bars)
        self._cache[timeframe] = df
        log.debug("Fetched %s: %d bars, last=%s", timeframe, len(df), df.index[-1] if len(df) else None)
        return df
