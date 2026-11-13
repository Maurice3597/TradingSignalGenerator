"""Builds the WhatsApp message text."""
from __future__ import annotations

from .config import TIMEFRAMES
from .strategy import BUY, NEUTRAL, SELL, Confluence, TimeframeSignal

ICON = {BUY: "🟢", SELL: "🔴", NEUTRAL: "⚪"}
LABEL = {BUY: "BUY", SELL: "SELL", NEUTRAL: "NEUTRAL (stay away)"}


def _tf(tf: str) -> str:
    return tf.upper()


def format_message(symbol: str, signals: dict[str, TimeframeSignal], confluences: list[Confluence]) -> str:
    price = next((s.price for s in (signals.get(t) for t in TIMEFRAMES) if s and s.price), None)
    lines = [f"📊 *GOLD ({symbol}) SIGNALS*"]
    if price is not None:
        lines.append(f"Price: {price:.2f}")
    lines.append("")

    if confluences:
        for c in confluences:
            lines.append(
                f"✅ *CONFLUENCE {c.direction}*: {_tf(c.lower)} & {_tf(c.higher)} both say {c.direction}."
            )
            lines.append(f"👉 Enter the {c.direction} trade on the *{_tf(c.entry_timeframe)}* timeframe.")
            s = signals[c.entry_timeframe]
            lines.append(f"   Entry ≈ {s.price:.2f} | SL {s.stop_loss:.2f} | TP1 {s.take_profit_1:.2f} | TP2 {s.take_profit_2:.2f}")
        lines.append("")
    else:
        lines.append("No adjacent-timeframe confluence.")
        lines.append("")

    for tf in TIMEFRAMES:
        s = signals.get(tf)
        if s is None:
            lines.append(f"⚠️ {_tf(tf)}: data unavailable")
            continue
        lines.append(f"{ICON[s.direction]} *{_tf(tf)}: {LABEL[s.direction]}* (score {s.score:+d})")
        v = s.values
        if v and s.candle_time is not None:
            lines.append(
                f"   RSI {v['rsi']:.1f} | ADX {v['adx']:.1f} | StochK {v['stoch_k']:.1f} | "
                f"MACD {'>' if v['macd'] > v['macd_signal'] else '<'} sig"
            )
        if s.direction != NEUTRAL:
            lines.append(f"   SL {s.stop_loss:.2f} | TP1 {s.take_profit_1:.2f} | TP2 {s.take_profit_2:.2f}")
        elif s.reason:
            lines.append(f"   {s.reason}")

    last = max((s.candle_time for s in signals.values() if s and s.candle_time is not None), default=None)
    lines.append("")
    if last is not None:
        lines.append(f"Last closed candle: {last.strftime('%Y-%m-%d %H:%M')} UTC")
    lines.append("⚠️ Not financial advice. Manage your risk.")
    return "\n".join(lines)
