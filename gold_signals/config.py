"""Configuration loaded from environment variables (optionally a ``.env`` file)."""
from __future__ import annotations

import os
from dataclasses import dataclass, field

try:  # python-dotenv is optional at runtime
    from dotenv import load_dotenv
except ImportError:  # pragma: no cover
    load_dotenv = None

# Timeframes analysed, lowest -> highest. Adjacent pairs are checked for confluence.
TIMEFRAMES: tuple[str, ...] = ("5m", "15m", "30m", "1h")
TIMEFRAME_MINUTES: dict[str, int] = {"5m": 5, "15m": 15, "30m": 30, "1h": 60}


def _env(name: str, default: str = "") -> str:
    return os.getenv(name, default).strip()


def _env_float(name: str, default: float) -> float:
    raw = _env(name)
    return float(raw) if raw else default


def _env_int(name: str, default: int) -> int:
    raw = _env(name)
    return int(raw) if raw else default


def _env_bool(name: str, default: bool) -> bool:
    raw = _env(name).lower()
    if not raw:
        return default
    return raw in {"1", "true", "yes", "on"}


@dataclass
class StrategyConfig:
    ema_fast: int = 50
    ema_slow: int = 200
    rsi_period: int = 14
    rsi_overbought: float = 70.0
    rsi_oversold: float = 30.0
    macd_fast: int = 12
    macd_slow: int = 26
    macd_signal: int = 9
    stoch_k: int = 14
    stoch_smooth: int = 3
    stoch_d: int = 3
    stoch_overbought: float = 80.0
    stoch_oversold: float = 20.0
    adx_period: int = 14
    adx_min: float = 20.0
    atr_period: int = 14
    # Minimum net score (sum of the 4 votes) required for BUY/SELL.
    min_votes: int = 3
    sl_atr_mult: float = 1.5
    tp1_atr_mult: float = 1.5
    tp2_atr_mult: float = 3.0

    @classmethod
    def from_env(cls) -> "StrategyConfig":
        return cls(
            adx_min=_env_float("ADX_MIN", cls.adx_min),
            min_votes=_env_int("MIN_VOTES", cls.min_votes),
            sl_atr_mult=_env_float("SL_ATR_MULT", cls.sl_atr_mult),
            tp1_atr_mult=_env_float("TP1_ATR_MULT", cls.tp1_atr_mult),
            tp2_atr_mult=_env_float("TP2_ATR_MULT", cls.tp2_atr_mult),
        )


@dataclass
class AppConfig:
    data_provider: str = "twelvedata"  # twelvedata | yahoo
    twelvedata_api_key: str = ""
    twelvedata_symbol: str = "XAU/USD"
    yahoo_symbol: str = "GC=F"
    bars: int = 500

    whatsapp_provider: str = "callmebot"  # callmebot | twilio | meta | console
    whatsapp_to: str = ""  # international format, e.g. +2376XXXXXXXX
    callmebot_api_key: str = ""
    twilio_account_sid: str = ""
    twilio_auth_token: str = ""
    twilio_whatsapp_from: str = "+14155238886"  # Twilio sandbox sender
    meta_access_token: str = ""
    meta_phone_number_id: str = ""
    meta_api_version: str = "v21.0"

    notify_mode: str = "change"  # change | confluence | always
    state_file: str = "state.json"
    data_cache_dir: str = ""  # persist downloaded candles between runs (GitHub Actions)
    candle_close_delay_s: int = 10
    skip_weekend: bool = True
    strategy: StrategyConfig = field(default_factory=StrategyConfig)

    @classmethod
    def load(cls, env_file: str | None = ".env") -> "AppConfig":
        if load_dotenv and env_file and os.path.exists(env_file):
            load_dotenv(env_file)
        return cls(
            data_provider=_env("DATA_PROVIDER", cls.data_provider).lower(),
            twelvedata_api_key=_env("TWELVEDATA_API_KEY"),
            twelvedata_symbol=_env("TWELVEDATA_SYMBOL", cls.twelvedata_symbol),
            yahoo_symbol=_env("YAHOO_SYMBOL", cls.yahoo_symbol),
            bars=_env_int("BARS", cls.bars),
            whatsapp_provider=_env("WHATSAPP_PROVIDER", cls.whatsapp_provider).lower(),
            whatsapp_to=_env("WHATSAPP_TO"),
            callmebot_api_key=_env("CALLMEBOT_API_KEY"),
            twilio_account_sid=_env("TWILIO_ACCOUNT_SID"),
            twilio_auth_token=_env("TWILIO_AUTH_TOKEN"),
            twilio_whatsapp_from=_env("TWILIO_WHATSAPP_FROM", cls.twilio_whatsapp_from),
            meta_access_token=_env("META_ACCESS_TOKEN"),
            meta_phone_number_id=_env("META_PHONE_NUMBER_ID"),
            meta_api_version=_env("META_API_VERSION", cls.meta_api_version),
            notify_mode=_env("NOTIFY_MODE", cls.notify_mode).lower(),
            state_file=_env("STATE_FILE", cls.state_file),
            data_cache_dir=_env("DATA_CACHE_DIR"),
            candle_close_delay_s=_env_int("CANDLE_CLOSE_DELAY_S", cls.candle_close_delay_s),
            skip_weekend=_env_bool("SKIP_WEEKEND", cls.skip_weekend),
            strategy=StrategyConfig.from_env(),
        )
