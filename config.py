"""
═══════════════════════════════════════════════════════════════
  DERIV SMART TRADER — MASTER CONFIGURATION
  Every single parameter is adjustable here.
═══════════════════════════════════════════════════════════════
"""
import os
from dotenv import load_dotenv

load_dotenv()

# ═══════════════════════════════════════════════════════════
# DERIV API CREDENTIALS
# ═══════════════════════════════════════════════════════════
DERIV_APP_ID = os.getenv("DERIV_APP_ID", "1089")       # Default is demo app_id
DERIV_API_TOKEN = os.getenv("DERIV_API_TOKEN", "")      # Your API token
DERIV_WS_URL = "wss://ws.derivws.com/websockets/v3?app_id={}"

# ═══════════════════════════════════════════════════════════
# TELEGRAM
# ═══════════════════════════════════════════════════════════
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")

# ═══════════════════════════════════════════════════════════
# DASHBOARD
# ═══════════════════════════════════════════════════════════
DASHBOARD_HOST = "0.0.0.0"     # 0.0.0.0 = accessible from other devices
DASHBOARD_PORT = 8080

# ═══════════════════════════════════════════════════════════
# VOLATILITY INDICES TO TRADE
# ═══════════════════════════════════════════════════════════
VOLATILITY_INDICES = {
    "R_10":    {"name": "Volatility 10",      "type": "standard", "noise": 0.10},
    "R_25":    {"name": "Volatility 25",      "type": "standard", "noise": 0.25},
    "R_50":    {"name": "Volatility 50",      "type": "standard", "noise": 0.50},
    "R_75":    {"name": "Volatility 75",      "type": "standard", "noise": 0.75},
    "R_100":   {"name": "Volatility 100",     "type": "standard", "noise": 1.00},
    "1HZ10V":  {"name": "Volatility 10 (1s)", "type": "1s",       "noise": 0.10},
    "1HZ25V":  {"name": "Volatility 25 (1s)", "type": "1s",       "noise": 0.25},
    "1HZ50V":  {"name": "Volatility 50 (1s)", "type": "1s",       "noise": 0.50},
    "1HZ75V":  {"name": "Volatility 75 (1s)", "type": "1s",       "noise": 0.75},
    "1HZ100V": {"name": "Volatility 100 (1s)","type": "1s",       "noise": 1.00},
}

# ═══════════════════════════════════════════════════════════
# INDICATOR SETTINGS (All adjustable)
# ═══════════════════════════════════════════════════════════
INDICATORS = {
    # EMA
    "ema_fast":             9,
    "ema_slow":             21,
    "ema_micro":            5,

    # RSI
    "rsi_period":           14,
    "rsi_bullish_zone":     45,
    "rsi_bearish_zone":     55,
    "rsi_overbought":       78,
    "rsi_oversold":         22,

    # MACD
    "macd_fast":            12,
    "macd_slow":            26,
    "macd_signal":          9,

    # ADX
    "adx_period":           14,
    "adx_trend_threshold":  20,
    "adx_strong_trend":     30,

    # Stochastic RSI
    "stoch_rsi_period":     14,
    "stoch_rsi_k":          3,
    "stoch_rsi_d":          3,
    "stoch_oversold":       20,
    "stoch_overbought":     80,

    # Bollinger Bands
    "bb_period":            20,
    "bb_std_dev":           2,

    # Tick Analysis
    "tmi_window":           25,
    "tmi_strong":           68,
    "tmi_minimum":          55,
    "streak_lookback":      100,
    "tick_accel_window":    15,
}

# ═══════════════════════════════════════════════════════════
# PATTERN DETECTION
# ═══════════════════════════════════════════════════════════
PATTERNS = {
    "retest_rise_count":    3,      # Rise-Rise-Rise = 3
    "retest_fall_count":    2,      # Fall-Fall max = 2
    "max_retrace_ratio":    0.618,  # Maximum Fibonacci retrace
}

# ═══════════════════════════════════════════════════════════
# GATE SYSTEM WEIGHTS
# ═══════════════════════════════════════════════════════════
GATE_WEIGHTS = {
    "trend":        0.30,
    "momentum":     0.25,
    "pattern":      0.25,
    "tick_purity":  0.20,
}

TIMEFRAME_WEIGHTS = {
    "candles_5m":   0.35,
    "candles_3m":   0.25,
    "candles_2m":   0.25,
    "candles_1m":   0.15,
}

MIN_COMPOSITE_SCORE = 65
HIGH_CONFIDENCE_SCORE = 80

# ═══════════════════════════════════════════════════════════
# TREND PURITY SCORE → TICK COUNT MAPPING
# ═══════════════════════════════════════════════════════════
TICK_COUNT_THRESHOLDS = {
    2: 40,
    3: 56,
    4: 71,
    5: 86,
}

# ═══════════════════════════════════════════════════════════
# RISK MANAGEMENT
# ═══════════════════════════════════════════════════════════
ACCOUNT_TIERS = {
    "MICRO":  {"min": 0,     "max": 25,    "base_risk_pct": 2,
               "max_recovery": 2, "compound_max": 1.0, "max_ticks": 2},
    "SMALL":  {"min": 25,    "max": 100,   "base_risk_pct": 3,
               "max_recovery": 3, "compound_max": 1.2, "max_ticks": 3},
    "MEDIUM": {"min": 100,   "max": 500,   "base_risk_pct": 4,
               "max_recovery": 4, "compound_max": 1.5, "max_ticks": 4},
    "LARGE":  {"min": 500,   "max": 2000,  "base_risk_pct": 5,
               "max_recovery": 4, "compound_max": 1.8, "max_ticks": 5},
    "WHALE":  {"min": 2000,  "max": float("inf"), "base_risk_pct": 5,
               "max_recovery": 5, "compound_max": 2.0, "max_ticks": 5},
}

RISK = {
    "daily_loss_limit_pct":     15,
    "session_drawdown_pct":     20,
    "max_single_trade_pct":     10,
    "min_stake":                0.35,
    "compound_trigger_wins":    4,
    "compound_max_multiplier":  3.0,
    "cooldown_after_losses":    2,      # Consecutive losses before cooldown
    "cooldown_seconds":         60,
    "min_trades_cushion":       5,      # Always keep enough for 5 more trades
}

# ═══════════════════════════════════════════════════════════
# PER-INDEX OVERRIDES (noisy indices get tighter filters)
# ═══════════════════════════════════════════════════════════
INDEX_OVERRIDES = {
    "R_75":    {"adx_trend_threshold": 25, "tmi_minimum": 62,
                "min_composite": 72, "tick_3_min": 65, "tick_5_min": 92},
    "R_100":   {"adx_trend_threshold": 25, "tmi_minimum": 62,
                "min_composite": 72, "tick_3_min": 65, "tick_5_min": 92},
    "1HZ75V":  {"adx_trend_threshold": 25, "tmi_minimum": 62,
                "min_composite": 72, "tick_3_min": 65, "tick_5_min": 92},
    "1HZ100V": {"adx_trend_threshold": 25, "tmi_minimum": 62,
                "min_composite": 72, "tick_3_min": 65, "tick_5_min": 92},
    "R_10":    {"adx_trend_threshold": 18, "tmi_minimum": 52,
                "min_composite": 60, "tick_3_min": 50, "tick_5_min": 80},
    "R_25":    {"adx_trend_threshold": 18, "tmi_minimum": 52,
                "min_composite": 60, "tick_3_min": 50, "tick_5_min": 80},
    "1HZ10V":  {"adx_trend_threshold": 18, "tmi_minimum": 52,
                "min_composite": 60, "tick_3_min": 50, "tick_5_min": 80},
    "1HZ25V":  {"adx_trend_threshold": 18, "tmi_minimum": 52,
                "min_composite": 60, "tick_3_min": 50, "tick_5_min": 80},
}

# ═══════════════════════════════════════════════════════════
# DATA SETTINGS
# ═══════════════════════════════════════════════════════════
DATA = {
    "tick_buffer_size":     5000,
    "candle_buffer_size":   100,
    "granularities":        [60, 120, 180, 300],   # 1m, 2m, 3m, 5m
}

# ═══════════════════════════════════════════════════════════
# LOGGING
# ═══════════════════════════════════════════════════════════
LOG_TRADES = True
LOG_INDICATORS = False    # Set True for debugging
LOG_FILE = "trades.log"