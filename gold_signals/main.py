"""Entry point: analyse 5m/15m/30m/1h gold charts and send WhatsApp alerts.

Usage:
    python -m gold_signals                # run forever, evaluating after every 5-minute close
    python -m gold_signals --once         # single evaluation (alerts only on change), then exit
    python -m gold_signals --once --force # single evaluation, always send
    python -m gold_signals --once --dry-run   # print message instead of sending
    python -m gold_signals --demo --once --dry-run   # synthetic data, no API keys needed
    python -m gold_signals --test-whatsapp    # send a test message to your WhatsApp
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import time
from datetime import datetime, timedelta, timezone

import numpy as np
import pandas as pd

from .config import TIMEFRAME_MINUTES, TIMEFRAMES, AppConfig
from .data import CachedFetcher, DataError, make_provider
from .formatter import format_message
from .notifier import ConsoleNotifier, NotifyError, make_notifier, send_with_retry
from .strategy import analyze, find_confluences

log = logging.getLogger("gold_signals")


def market_closed(now: datetime) -> bool:
    """Approximate spot-gold weekly closure: Fri 22:00 UTC -> Sun 22:00 UTC."""
    wd = now.weekday()  # Mon=0 .. Sun=6
    return wd == 5 or (wd == 4 and now.hour >= 22) or (wd == 6 and now.hour < 22)


class DemoProvider:
    """Synthetic random-walk OHLC data for testing without API keys."""

    def __init__(self, seed: int = 7):
        self.seed = seed

    def fetch(self, timeframe: str, bars: int = 500) -> pd.DataFrame:
        minutes = TIMEFRAME_MINUTES[timeframe]
        rng = np.random.default_rng(self.seed + minutes)
        end = pd.Timestamp.now(tz="UTC").floor(f"{minutes}min") - pd.Timedelta(minutes=minutes)
        idx = pd.date_range(end=end, periods=bars, freq=f"{minutes}min")
        drift = 0.02 * np.sqrt(minutes)
        steps = rng.normal(drift, 1.2 * np.sqrt(minutes), bars)
        close = 2650 + np.cumsum(steps)
        open_ = np.r_[close[0], close[:-1]]
        spread = np.abs(rng.normal(0, 0.8 * np.sqrt(minutes), bars))
        return pd.DataFrame(
            {"open": open_, "high": np.maximum(open_, close) + spread,
             "low": np.minimum(open_, close) - spread, "close": close},
            index=idx,
        )


def load_state(path: str) -> dict:
    try:
        with open(path) as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def save_state(path: str, state: dict) -> None:
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(state, f, indent=2)
    os.replace(tmp, path)


def fingerprint(signals, confluences) -> dict:
    return {
        "directions": {tf: signals[tf].direction for tf in TIMEFRAMES if tf in signals},
        "confluences": [f"{c.lower}/{c.higher}:{c.direction}" for c in confluences],
    }


def should_notify(mode: str, prev: dict | None, cur: dict) -> bool:
    if mode == "always":
        return True
    if mode == "confluence":
        return bool(cur["confluences"]) and cur["confluences"] != (prev or {}).get("confluences")
    return cur != prev  # "change"


def evaluate(fetcher: CachedFetcher, cfg: AppConfig, symbol: str):
    signals = {}
    for tf in TIMEFRAMES:
        try:
            df = fetcher.get(tf)
            signals[tf] = analyze(df, tf, cfg.strategy)
        except Exception as e:  # noqa: BLE001 - keep other timeframes running
            log.error("Failed to analyse %s: %s", tf, e)
    confluences = find_confluences(signals)
    return signals, confluences, format_message(symbol, signals, confluences)


def run_cycle(fetcher, notifier, cfg: AppConfig, symbol: str, force_send: bool = False) -> tuple[str, bool]:
    """Evaluate all timeframes and alert if needed. Returns (message, ok); ok is False when
    some timeframe could not be analysed or an alert could not be delivered."""
    signals, confluences, text = evaluate(fetcher, cfg, symbol)
    for tf, s in signals.items():
        log.info("%-3s %-7s score=%+d %s", tf, s.direction, s.score, s.reason)
    for c in confluences:
        log.info("CONFLUENCE %s/%s %s -> enter on %s", c.lower, c.higher, c.direction, c.lower)

    if not signals:
        log.warning("No data for any timeframe; nothing sent")
        return text, False
    ok = len(signals) == len(TIMEFRAMES)
    state = load_state(cfg.state_file)
    cur = fingerprint(signals, confluences)
    if force_send or should_notify(cfg.notify_mode, state.get("last"), cur):
        if send_with_retry(notifier, text):
            log.info("Alert sent")
            save_state(cfg.state_file, {"last": cur, "sent_at": datetime.now(timezone.utc).isoformat()})
        else:
            log.error("Alert could not be delivered; will retry next cycle")
            ok = False
    else:
        log.info("No change in signals; no alert sent")
    return text, ok


def seconds_to_next_close(now: datetime, delay_s: int) -> float:
    nxt = now.replace(second=0, microsecond=0) - timedelta(minutes=now.minute % 5) + timedelta(minutes=5)
    return max(1.0, (nxt - now).total_seconds() + delay_s)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Gold multi-timeframe signal generator")
    ap.add_argument("--once", action="store_true", help="run a single evaluation and exit")
    ap.add_argument("--force", action="store_true",
                    help="with --once: send even if nothing changed or the market is closed")
    ap.add_argument("--dry-run", action="store_true", help="print alerts instead of sending to WhatsApp")
    ap.add_argument("--demo", action="store_true", help="use synthetic data (no API key needed)")
    ap.add_argument("--test-whatsapp", action="store_true", help="send a test WhatsApp message and exit")
    ap.add_argument("--env-file", default=".env")
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args(argv)

    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    cfg = AppConfig.load(args.env_file)

    try:
        notifier = ConsoleNotifier() if args.dry_run else make_notifier(cfg)
        provider = DemoProvider() if args.demo else make_provider(cfg)
    except (NotifyError, DataError) as e:
        log.error("Configuration error: %s", e)
        return 2
    if args.test_whatsapp:
        ok = send_with_retry(notifier, "✅ Gold signal generator: WhatsApp test message.")
        print("Sent" if ok else "FAILED")
        return 0 if ok else 1

    if args.demo:
        symbol = "DEMO"
        cfg.state_file = cfg.state_file + ".demo"
    else:
        symbol = cfg.twelvedata_symbol if cfg.data_provider == "twelvedata" else cfg.yahoo_symbol
    fetcher = CachedFetcher(provider, cfg.bars, cfg.data_cache_dir or None)

    if args.once:
        if (cfg.skip_weekend and not args.demo and not args.force
                and market_closed(datetime.now(timezone.utc))):
            log.info("Gold market closed (weekend); nothing to do")
            return 0
        _, ok = run_cycle(fetcher, notifier, cfg, symbol, force_send=args.force or args.dry_run)
        return 0 if ok else 1

    log.info("Started: provider=%s whatsapp=%s mode=%s", cfg.data_provider, cfg.whatsapp_provider, cfg.notify_mode)
    while True:
        now = datetime.now(timezone.utc)
        if cfg.skip_weekend and market_closed(now) and not args.demo:
            log.info("Gold market closed (weekend); sleeping 30 min")
            time.sleep(1800)
            continue
        try:
            run_cycle(fetcher, notifier, cfg, symbol)
        except Exception:  # noqa: BLE001 - never let the loop die
            log.exception("Cycle failed")
        wait = seconds_to_next_close(datetime.now(timezone.utc), cfg.candle_close_delay_s)
        log.debug("Sleeping %.0fs", wait)
        time.sleep(wait)


if __name__ == "__main__":
    raise SystemExit(main())
