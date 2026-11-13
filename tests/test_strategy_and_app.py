from datetime import datetime, timezone
from unittest.mock import MagicMock

import numpy as np
import pandas as pd
import pytest

from gold_signals.config import AppConfig, StrategyConfig
from gold_signals.data import CachedFetcher, TwelveDataProvider, YahooProvider, drop_unclosed
from gold_signals.formatter import format_message
from gold_signals.main import market_closed, run_cycle, seconds_to_next_close, should_notify
from gold_signals.notifier import CallMeBotNotifier, MetaCloudNotifier, TwilioNotifier, _chunks
from gold_signals.strategy import BUY, NEUTRAL, SELL, TimeframeSignal, analyze, find_confluences


def trend_df(direction=1, n=400, minutes=5):
    """Deterministic trend + oscillation, then a pullback (8 bars) and a resumption (7 bars),
    so trend, MACD and RSI confirm while oscillators are not yet at extremes."""
    t = np.arange(n)
    close = 2000 + direction * (0.15 * t + 2 * np.sin(t / 3))
    base = close[-16]
    close[-15:] = base + direction * np.r_[-0.4 * np.arange(1, 9), -3.2 + 0.5 * np.arange(1, 8)]
    open_ = np.r_[close[0], close[:-1]]
    return pd.DataFrame({"open": open_, "high": np.maximum(open_, close) + 0.5,
                         "low": np.minimum(open_, close) - 0.5, "close": close},
                        index=pd.date_range("2025-01-06", periods=n, freq=f"{minutes}min", tz="UTC"))


def test_uptrend_gives_buy_with_sl_tp():
    s = analyze(trend_df(+1), "5m")
    assert s.direction == BUY, (s.votes, s.values, s.reason)
    assert s.stop_loss < s.price < s.take_profit_1 < s.take_profit_2


def test_downtrend_gives_sell():
    s = analyze(trend_df(-1), "5m")
    assert s.direction == SELL, (s.votes, s.values, s.reason)
    assert s.stop_loss > s.price > s.take_profit_1 > s.take_profit_2


def test_flat_market_is_neutral():
    rng = np.random.default_rng(3)
    close = 2000 + rng.normal(0, 0.2, 400)
    df = pd.DataFrame({"open": close, "high": close + 0.3, "low": close - 0.3, "close": close},
                      index=pd.date_range("2025-01-06", periods=400, freq="5min", tz="UTC"))
    assert analyze(df, "5m").direction == NEUTRAL


def test_insufficient_data_is_neutral():
    s = analyze(trend_df(+1, n=50), "5m")
    assert s.direction == NEUTRAL and "not enough data" in s.reason


def test_adx_filter_blocks_signal():
    cfg = StrategyConfig(adx_min=99)
    assert analyze(trend_df(+1), "5m", cfg).direction == NEUTRAL


def _sig(tf, d):
    return TimeframeSignal(tf, d, 0, pd.Timestamp("2025-01-06", tz="UTC"), 2000.0,
                           stop_loss=1990.0, take_profit_1=2010.0, take_profit_2=2020.0)


def test_confluence_adjacent_pairs_only():
    sig = {"5m": _sig("5m", BUY), "15m": _sig("15m", BUY), "30m": _sig("30m", NEUTRAL), "1h": _sig("1h", BUY)}
    c = find_confluences(sig)
    assert [(x.lower, x.higher, x.direction) for x in c] == [("5m", "15m", BUY)]
    assert c[0].entry_timeframe == "5m"


def test_confluence_all_three_pairs_and_message():
    sig = {tf: _sig(tf, SELL) for tf in ("5m", "15m", "30m", "1h")}
    c = find_confluences(sig)
    assert [(x.lower, x.higher) for x in c] == [("5m", "15m"), ("15m", "30m"), ("30m", "1h")]
    msg = format_message("XAU/USD", sig, c)
    assert "5M & 15M both say SELL" in msg and "Enter the SELL trade on the *5M* timeframe" in msg
    assert "15M & 30M" in msg and "*15M*" in msg
    assert "30M & 1H" in msg and "*30M*" in msg


def test_opposite_signals_no_confluence():
    sig = {"5m": _sig("5m", BUY), "15m": _sig("15m", SELL)}
    assert find_confluences(sig) == []


def test_drop_unclosed():
    idx = pd.date_range("2025-01-06 10:00", periods=3, freq="5min", tz="UTC")
    df = pd.DataFrame({"close": [1, 2, 3]}, index=idx)
    out = drop_unclosed(df, "5m", now=datetime(2025, 1, 6, 10, 12, tzinfo=timezone.utc))
    assert list(out.close) == [1, 2]  # 10:10 bar closes at 10:15 -> still forming


def test_twelvedata_parsing():
    sess = MagicMock()
    sess.get.return_value.json.return_value = {"status": "ok", "values": [
        {"datetime": "2025-01-06 10:00:00", "open": "1", "high": "2", "low": "0.5", "close": "1.5"},
        {"datetime": "2025-01-06 10:05:00", "open": "1.5", "high": "2", "low": "1", "close": "1.8"}]}
    df = TwelveDataProvider("k", session=sess).fetch("5m", 2)
    assert list(df.close) == [1.5, 1.8] and str(df.index.tz) == "UTC"
    params = sess.get.call_args.kwargs["params"]
    assert params["symbol"] == "XAU/USD" and params["interval"] == "5min" and params["timezone"] == "UTC"


def test_twelvedata_error_raises():
    sess = MagicMock()
    sess.get.return_value.json.return_value = {"status": "error", "code": 429, "message": "limit"}
    with pytest.raises(Exception, match="429"):
        TwelveDataProvider("k", session=sess).fetch("5m")


def test_yahoo_parsing():
    sess = MagicMock()
    sess.get.return_value.json.return_value = {"chart": {"error": None, "result": [{
        "timestamp": [1736157600, 1736157900],
        "indicators": {"quote": [{"open": [1, 2], "high": [2, 3], "low": [0, 1], "close": [1.5, None]}]}}]}}
    df = YahooProvider(session=sess).fetch("5m")
    assert list(df.close) == [1.5]


def test_cache_refetches_only_after_new_close():
    prov = MagicMock()
    idx = pd.date_range("2025-01-06 10:00", periods=2, freq="15min", tz="UTC")
    prov.fetch.return_value = pd.DataFrame({"close": [1, 2]}, index=idx)  # last bar 10:15 closes 10:30
    f = CachedFetcher(prov)
    f.get("15m", now=datetime(2025, 1, 6, 10, 31, tzinfo=timezone.utc))
    f.get("15m", now=datetime(2025, 1, 6, 10, 40, tzinfo=timezone.utc))
    assert prov.fetch.call_count == 1
    f.get("15m", now=datetime(2025, 1, 6, 10, 45, tzinfo=timezone.utc))
    assert prov.fetch.call_count == 2


def test_callmebot_request():
    sess = MagicMock()
    sess.get.return_value.status_code = 200
    sess.get.return_value.text = "Message queued"
    CallMeBotNotifier("237 6 1234 5678", "KEY", session=sess).send("hi")
    p = sess.get.call_args.kwargs["params"]
    assert p == {"phone": "+23761234567" + "8", "text": "hi", "apikey": "KEY"}


def test_twilio_request():
    sess = MagicMock()
    sess.post.return_value.status_code = 201
    TwilioNotifier("AC1", "tok", "+14155238886", "+237612345678", session=sess).send("hi")
    kw = sess.post.call_args.kwargs
    assert sess.post.call_args.args[0].endswith("/Accounts/AC1/Messages.json")
    assert kw["data"] == {"From": "whatsapp:+14155238886", "To": "whatsapp:+237612345678", "Body": "hi"}
    assert kw["auth"] == ("AC1", "tok")


def test_meta_request():
    sess = MagicMock()
    sess.post.return_value.status_code = 200
    MetaCloudNotifier("T", "PID", "+237612345678", session=sess).send("hi")
    kw = sess.post.call_args.kwargs
    assert kw["json"]["to"] == "237612345678" and kw["json"]["text"]["body"] == "hi"
    assert kw["headers"]["Authorization"] == "Bearer T"


def test_chunks():
    text = "\n".join(["x" * 30] * 10)
    parts = list(_chunks(text, 100))
    assert all(len(p) <= 100 for p in parts) and "".join(parts) == text


def test_should_notify_modes():
    a = {"directions": {"5m": BUY}, "confluences": []}
    b = {"directions": {"5m": SELL}, "confluences": ["5m/15m:SELL"]}
    assert should_notify("change", a, b) and not should_notify("change", b, b)
    assert should_notify("confluence", a, b) and not should_notify("confluence", b, a)
    assert should_notify("always", b, b)


def test_market_closed():
    assert market_closed(datetime(2025, 1, 11, 12, tzinfo=timezone.utc))      # Saturday
    assert market_closed(datetime(2025, 1, 10, 22, 30, tzinfo=timezone.utc))  # Fri late
    assert not market_closed(datetime(2025, 1, 12, 22, 30, tzinfo=timezone.utc))  # Sun open
    assert not market_closed(datetime(2025, 1, 8, 12, tzinfo=timezone.utc))


def test_seconds_to_next_close():
    now = datetime(2025, 1, 8, 12, 3, 30, tzinfo=timezone.utc)
    assert seconds_to_next_close(now, 10) == 100


def test_run_cycle_sends_once_then_dedups(tmp_path):
    cfg = AppConfig(state_file=str(tmp_path / "s.json"))
    prov = MagicMock()
    prov.fetch.side_effect = lambda tf, bars: trend_df(+1, minutes={"5m": 5, "15m": 15, "30m": 30, "1h": 60}[tf])
    notifier = MagicMock()
    fetcher = CachedFetcher(prov)
    text, ok = run_cycle(fetcher, notifier, cfg, "XAU/USD")
    assert ok and notifier.send.call_count == 1 and "CONFLUENCE BUY" in text
    run_cycle(fetcher, notifier, cfg, "XAU/USD")
    assert notifier.send.call_count == 1  # unchanged -> no duplicate alert


def test_disk_cache_survives_new_process(tmp_path):
    idx = pd.date_range("2025-01-06 10:00", periods=2, freq="15min", tz="UTC")
    prov = MagicMock()
    prov.fetch.return_value = pd.DataFrame(
        {"open": [1.0, 2], "high": [2.0, 3], "low": [0.5, 1], "close": [1.5, 2.5]}, index=idx)
    CachedFetcher(prov, cache_dir=str(tmp_path)).get("15m", now=datetime(2025, 1, 6, 10, 31, tzinfo=timezone.utc))
    # A brand-new fetcher (next GitHub Actions run) reuses the file instead of calling the API
    df = CachedFetcher(prov, cache_dir=str(tmp_path)).get("15m", now=datetime(2025, 1, 6, 10, 40, tzinfo=timezone.utc))
    assert prov.fetch.call_count == 1
    pd.testing.assert_frame_equal(df, prov.fetch.return_value, check_freq=False, check_names=False)


def test_once_cli_dedups_between_runs(tmp_path, monkeypatch):
    from gold_signals import main as m
    monkeypatch.setenv("WHATSAPP_PROVIDER", "console")
    monkeypatch.setenv("STATE_FILE", str(tmp_path / "state.json"))
    sent = []
    monkeypatch.setattr(m.ConsoleNotifier, "send", lambda self, t: sent.append(t))
    args = ["--demo", "--once", "--env-file", str(tmp_path / "none.env")]
    assert m.main(args) == 0 and len(sent) == 1
    assert m.main(args) == 0 and len(sent) == 1   # nothing changed -> no second alert
    assert m.main(args + ["--force"]) == 0 and len(sent) == 2
