# Gold (XAU/USD) Multi-Timeframe Signal Generator

A Python app that checks gold on the **5m, 15m, 30m and 1h** timeframes and decides
**BUY**, **SELL** or **NEUTRAL (stay away)** for each one. It sends the result to your
**WhatsApp**. When two neighbouring timeframes agree, the alert says so and tells you to
enter on the **lower** timeframe:

| Pair agrees    | Alert says                 |
|----------------|----------------------------|
| 5m & 15m       | enter on **5m**            |
| 15m & 30m      | enter on **15m**           |
| 30m & 1h       | enter on **30m**           |

> ⚠️ This tool is for information only, not financial advice. The strategy has **not** been
> backtested on real gold data. Test it on a demo account before you risk money.

## Strategy (5 indicators + ATR)

Each indicator is calculated on the **last closed candle** of each timeframe. The candle
that is still forming is dropped, so signals do not repaint.

| # | Indicator | Bullish vote (+1) | Bearish vote (−1) |
|---|-----------|-------------------|-------------------|
| 1 | EMA 50 / EMA 200 trend | EMA50 > EMA200 and close > EMA50 | EMA50 < EMA200 and close < EMA50 |
| 2 | MACD (12, 26, 9) | MACD > signal | MACD < signal |
| 3 | RSI (14) | 50 < RSI < 70 | 30 < RSI < 50 |
| 4 | Stochastic (14, 3, 3) | %K > %D and %K < 80 | %K < %D and %K > 20 |
| 5 | ADX / DMI (14), filter | ADX ≥ 20 and +DI > −DI | ADX ≥ 20 and −DI > +DI |

* **BUY** when the net score of indicators 1–4 is **≥ +3** (at least 3 bullish and none bearish)
  **and** the ADX filter confirms an uptrend.
* **SELL** when the net score is **≤ −3** and the ADX filter confirms a downtrend.
* Anything else is **NEUTRAL (stay away)**: the indicators disagree, or the market is ranging.
* **Stop loss / take profit** come from ATR(14): SL = 1.5×ATR, TP1 = 1.5×ATR (1R), TP2 = 3×ATR (2R).

You can change all thresholds in `.env`.

Indicator formulas follow the standard definitions:
* RSI, ATR, ADX/DMI: J. Welles Wilder Jr., *New Concepts in Technical Trading Systems* (1978)
  — see [StockCharts: RSI](https://chartschool.stockcharts.com/table-of-contents/technical-indicators-and-overlays/technical-indicators/relative-strength-index-rsi),
  [ATR](https://chartschool.stockcharts.com/table-of-contents/technical-indicators-and-overlays/technical-indicators/average-true-range-atr-and-average-true-range-percent-atrp),
  [ADX](https://chartschool.stockcharts.com/table-of-contents/technical-indicators-and-overlays/technical-indicators/average-directional-index-adx)
* MACD: [StockCharts: MACD](https://chartschool.stockcharts.com/table-of-contents/technical-indicators-and-overlays/technical-indicators/macd-moving-average-convergence-divergence-oscillator)
* Stochastic: [StockCharts: Stochastic Oscillator](https://chartschool.stockcharts.com/table-of-contents/technical-indicators-and-overlays/technical-indicators/stochastic-oscillator-fast-slow-and-full)

`tests/test_indicators.py` checks each indicator against a separate loop-based version of
the same formula.

## Setup

```bash
python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env    # then edit .env
```

### 1. Market data

* **Twelve Data** (recommended, spot `XAU/USD`): get a key at <https://twelvedata.com>, then
  set `TWELVEDATA_API_KEY`. The app only re-downloads a timeframe after one of its candles has
  closed, so it uses about **456 requests a day**. The free plan allows 8 per minute and
  800 per day ([Twelve Data pricing](https://twelvedata.com/pricing)). Check that your plan
  includes XAU/USD. If it does not, the API returns an error, which the app logs.
* **Yahoo** (`DATA_PROVIDER=yahoo`): needs no key, but uses **COMEX gold futures (GC=F)**,
  not spot. Prices differ slightly from your broker's XAU/USD. The endpoint is unofficial.

### 2. WhatsApp (choose one)

* **CallMeBot** (free, personal use, easiest): follow
  <https://www.callmebot.com/blog/free-api-whatsapp-messages/>. You send an activation
  message to their bot from your WhatsApp, and it replies with an API key. Set
  `WHATSAPP_PROVIDER=callmebot`, `WHATSAPP_TO`, `CALLMEBOT_API_KEY`.
* **Twilio**: <https://www.twilio.com/docs/whatsapp/quickstart>. With the sandbox, first send
  the `join <code>` message from your phone to +1 415 523 8886. Set `WHATSAPP_PROVIDER=twilio`
  and the `TWILIO_*` variables.
* **Meta WhatsApp Cloud API**: <https://developers.facebook.com/docs/whatsapp/cloud-api>.
  Free-form text is only delivered inside the 24-hour customer-service window. Outside it,
  WhatsApp requires an approved template.

Test delivery:

```bash
python -m gold_signals --test-whatsapp
```

## Running

```bash
python -m gold_signals --demo --once --dry-run   # synthetic data, prints the message (no keys)
python -m gold_signals --once --dry-run          # real data, prints instead of sending
python -m gold_signals --once                    # real data, sends one WhatsApp alert
python -m gold_signals                           # runs forever, every 5-minute candle close
```

The loop runs about 10 s after each 5-minute close (UTC). It skips the weekly gold closure
(about Fri 22:00 → Sun 22:00 UTC). With `NOTIFY_MODE=change` (default) it only messages you
when a signal or confluence changes. The last alert is stored in `state.json`.

To keep it running 24/5, use a VPS with `nohup`, `tmux`, a systemd service, or similar.

### Example alert

```
📊 *GOLD (XAU/USD) SIGNALS*
Price: 2655.40

✅ *CONFLUENCE BUY*: 5M & 15M both say BUY.
👉 Enter the BUY trade on the *5M* timeframe.
   Entry ≈ 2655.40 | SL 2652.10 | TP1 2658.70 | TP2 2662.00

🟢 *5M: BUY* (score +3)
   RSI 61.2 | ADX 24.8 | StochK 66.0 | MACD > sig
   SL 2652.10 | TP1 2658.70 | TP2 2662.00
🟢 *15M: BUY* (score +4)
   ...
⚪ *30M: NEUTRAL (stay away)* (score +1)
   indicators disagree (score +1)
⚪ *1H: NEUTRAL (stay away)* (score +2)
   bullish votes but ADX filter failed (ADX 17.3)
```

## Tests

```bash
pip install -r requirements-dev.txt
pytest -q
```

## Project layout

```
gold_signals/
  config.py      settings from .env
  data.py        Twelve Data / Yahoo download, closed-candle filter, request-saving cache
  indicators.py  EMA, RSI, MACD, Stochastic, ATR, ADX/DMI
  strategy.py    per-timeframe voting + adjacent-timeframe confluence
  formatter.py   WhatsApp message text
  notifier.py    CallMeBot / Twilio / Meta Cloud API / console
  main.py        CLI, scheduler loop, de-duplication
tests/
```
