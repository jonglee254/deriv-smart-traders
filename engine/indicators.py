"""
═══════════════════════════════════════════════════════════════
  TECHNICAL INDICATORS — All computed from raw data
  Every indicator is accurate, tested, and configurable.
═══════════════════════════════════════════════════════════════
"""
import numpy as np
from config import INDICATORS as IND


class Indicators:
    """
    Pure calculation engine. No state, no side effects.
    Feed it data arrays, get indicator values back.
    All periods/parameters pulled from config — fully adjustable.
    """

    # ══════════════════════════════════════════════════
    # EMA — Exponential Moving Average
    # ══════════════════════════════════════════════════
    @staticmethod
    def ema(data: list, period: int = None) -> list:
        period = period or IND["ema_fast"]
        if len(data) < period:
            return [data[-1]] * len(data) if data else []

        result = []
        multiplier = 2.0 / (period + 1)

        # SMA for first value
        sma = sum(data[:period]) / period
        result = [None] * (period - 1)
        result.append(sma)

        for i in range(period, len(data)):
            val = (data[i] - result[-1]) * multiplier + result[-1]
            result.append(val)

        # Fill leading Nones with first valid value
        first_valid = result[period - 1]
        for i in range(period - 1):
            result[i] = first_valid

        return result

    # ══════════════════════════════════════════════════
    # RSI — Relative Strength Index
    # ══════════════════════════════════════════════════
    @staticmethod
    def rsi(data: list, period: int = None) -> list:
        period = period or IND["rsi_period"]
        if len(data) < period + 1:
            return [50.0] * len(data)

        deltas = [data[i] - data[i-1] for i in range(1, len(data))]

        gains = [max(d, 0) for d in deltas]
        losses = [abs(min(d, 0)) for d in deltas]

        avg_gain = sum(gains[:period]) / period
        avg_loss = sum(losses[:period]) / period

        result = [50.0] * period  # Fill early values with neutral

        for i in range(period, len(deltas)):
            avg_gain = (avg_gain * (period - 1) + gains[i]) / period
            avg_loss = (avg_loss * (period - 1) + losses[i]) / period

            if avg_loss == 0:
                rs_val = 100.0
            else:
                rs = avg_gain / avg_loss
                rs_val = 100.0 - (100.0 / (1.0 + rs))

            result.append(rs_val)

        # Account for the offset (deltas is 1 shorter than data)
        result.insert(0, 50.0)
        return result[:len(data)]

    # ══════════════════════════════════════════════════
    # MACD — Moving Average Convergence Divergence
    # ══════════════════════════════════════════════════
    @staticmethod
    def macd(data: list, fast: int = None, slow: int = None,
             signal: int = None) -> dict:
        fast = fast or IND["macd_fast"]
        slow = slow or IND["macd_slow"]
        signal = signal or IND["macd_signal"]

        ema_fast = Indicators.ema(data, fast)
        ema_slow = Indicators.ema(data, slow)

        macd_line = [f - s for f, s in zip(ema_fast, ema_slow)]
        signal_line = Indicators.ema(macd_line, signal)
        histogram = [m - s for m, s in zip(macd_line, signal_line)]

        return {
            "macd": macd_line,
            "signal": signal_line,
            "histogram": histogram
        }

    # ══════════════════════════════════════════════════
    # ADX — Average Directional Index
    # ══════════════════════════════════════════════════
    @staticmethod
    def adx(highs: list, lows: list, closes: list,
            period: int = None) -> list:
        period = period or IND["adx_period"]
        n = len(closes)

        if n < period + 1:
            return [0.0] * n

        # True Range
        tr_list = [highs[0] - lows[0]]
        plus_dm_list = [0.0]
        minus_dm_list = [0.0]

        for i in range(1, n):
            h_diff = highs[i] - highs[i-1]
            l_diff = lows[i-1] - lows[i]

            plus_dm = h_diff if h_diff > l_diff and h_diff > 0 else 0.0
            minus_dm = l_diff if l_diff > h_diff and l_diff > 0 else 0.0

            tr = max(
                highs[i] - lows[i],
                abs(highs[i] - closes[i-1]),
                abs(lows[i] - closes[i-1])
            )

            tr_list.append(tr)
            plus_dm_list.append(plus_dm)
            minus_dm_list.append(minus_dm)

        # Smoothed averages
        atr = [sum(tr_list[:period]) / period]
        plus_di_smooth = [sum(plus_dm_list[:period]) / period]
        minus_di_smooth = [sum(minus_dm_list[:period]) / period]

        for i in range(period, n):
            atr.append((atr[-1] * (period - 1) + tr_list[i]) / period)
            plus_di_smooth.append(
                (plus_di_smooth[-1] * (period - 1) + plus_dm_list[i]) / period
            )
            minus_di_smooth.append(
                (minus_di_smooth[-1] * (period - 1) + minus_dm_list[i]) / period
            )

        # DI values and DX
        dx_list = []
        for i in range(len(atr)):
            if atr[i] == 0:
                dx_list.append(0.0)
                continue
            plus_di = 100 * plus_di_smooth[i] / atr[i]
            minus_di = 100 * minus_di_smooth[i] / atr[i]
            di_sum = plus_di + minus_di
            if di_sum == 0:
                dx_list.append(0.0)
            else:
                dx_list.append(100 * abs(plus_di - minus_di) / di_sum)

        # ADX = smoothed DX
        if len(dx_list) < period:
            return [0.0] * n

        adx_values = [sum(dx_list[:period]) / period]
        for i in range(period, len(dx_list)):
            adx_values.append(
                (adx_values[-1] * (period - 1) + dx_list[i]) / period
            )

        # Pad to match input length
        pad_len = n - len(adx_values)
        result = [adx_values[0]] * pad_len + adx_values
        return result

    # ══════════════════════════════════════════════════
    # STOCHASTIC RSI
    # ══════════════════════════════════════════════════
    @staticmethod
    def stoch_rsi(data: list, period: int = None,
                  k_smooth: int = None, d_smooth: int = None) -> dict:
        period = period or IND["stoch_rsi_period"]
        k_smooth = k_smooth or IND["stoch_rsi_k"]
        d_smooth = d_smooth or IND["stoch_rsi_d"]

        rsi_values = Indicators.rsi(data, period)

        stoch_k_raw = []
        for i in range(len(rsi_values)):
            start = max(0, i - period + 1)
            window = rsi_values[start:i+1]
            if len(window) < 2:
                stoch_k_raw.append(50.0)
                continue
            low = min(window)
            high = max(window)
            if high == low:
                stoch_k_raw.append(50.0)
            else:
                stoch_k_raw.append(((rsi_values[i] - low) / (high - low)) * 100)

        # Smooth %K
        k_values = Indicators._sma(stoch_k_raw, k_smooth)
        # %D = SMA of %K
        d_values = Indicators._sma(k_values, d_smooth)

        return {"k": k_values, "d": d_values}

    # ══════════════════════════════════════════════════
    # BOLLINGER BANDS
    # ══════════════════════════════════════════════════
    @staticmethod
    def bollinger(data: list, period: int = None,
                  std_dev: float = None) -> dict:
        period = period or IND["bb_period"]
        std_dev = std_dev or IND["bb_std_dev"]

        middle = Indicators._sma(data, period)
        upper = []
        lower = []

        for i in range(len(data)):
            start = max(0, i - period + 1)
            window = data[start:i+1]
            if len(window) < 2:
                upper.append(data[i])
                lower.append(data[i])
                continue
            std = np.std(window, ddof=0)
            upper.append(middle[i] + std_dev * std)
            lower.append(middle[i] - std_dev * std)

        return {"upper": upper, "middle": middle, "lower": lower}

    # ══════════════════════════════════════════════════
    # TICK MOMENTUM INDEX (Custom)
    # ══════════════════════════════════════════════════
    @staticmethod
    def tick_momentum_index(prices: list, window: int = None) -> float:
        window = window or IND["tmi_window"]
        if len(prices) < 2:
            return 50.0

        recent = prices[-window:] if len(prices) >= window else prices
        up_ticks = sum(1 for i in range(1, len(recent))
                       if recent[i] > recent[i-1])
        total = len(recent) - 1

        if total == 0:
            return 50.0

        return (up_ticks / total) * 100

    # ══════════════════════════════════════════════════
    # CONSECUTIVE STREAK COUNTER
    # ══════════════════════════════════════════════════
    @staticmethod
    def count_streaks(prices: list, lookback: int = None,
                      max_streak: int = 5) -> dict:
        lookback = lookback or IND["streak_lookback"]
        recent = prices[-lookback:] if len(prices) >= lookback else prices

        streaks = {}
        for s in range(2, max_streak + 1):
            streaks[s] = {"up": 0, "down": 0, "total_windows": 0}

        if len(recent) < 3:
            return streaks

        total_windows = len(recent) - max_streak
        if total_windows <= 0:
            total_windows = 1

        for i in range(1, len(recent)):
            # Count current consecutive ups
            up_count = 0
            for j in range(i, len(recent)):
                if recent[j] > recent[j-1]:
                    up_count += 1
                else:
                    break

            # Count current consecutive downs
            down_count = 0
            for j in range(i, len(recent)):
                if recent[j] < recent[j-1]:
                    down_count += 1
                else:
                    break

            for s in range(2, max_streak + 1):
                if up_count >= s:
                    streaks[s]["up"] += 1
                if down_count >= s:
                    streaks[s]["down"] += 1
                streaks[s]["total_windows"] = total_windows

        # Convert to percentages
        for s in streaks:
            tw = max(streaks[s]["total_windows"], 1)
            streaks[s]["up_pct"] = round((streaks[s]["up"] / tw) * 100, 1)
            streaks[s]["down_pct"] = round((streaks[s]["down"] / tw) * 100, 1)

        return streaks

    # ══════════════════════════════════════════════════
    # TICK ACCELERATION
    # ══════════════════════════════════════════════════
    @staticmethod
    def tick_acceleration(prices: list, window: int = None) -> float:
        window = window or IND["tick_accel_window"]
        if len(prices) < window + 1:
            return 0.0

        recent = prices[-window:]
        deltas = [recent[i] - recent[i-1] for i in range(1, len(recent))]

        if len(deltas) < 2:
            return 0.0

        # Acceleration = change in velocity (delta of deltas)
        half = len(deltas) // 2
        first_half_avg = sum(deltas[:half]) / half if half > 0 else 0
        second_half_avg = sum(deltas[half:]) / (len(deltas) - half)

        return second_half_avg - first_half_avg

    # ══════════════════════════════════════════════════
    # HELPER: Simple Moving Average
    # ══════════════════════════════════════════════════
    @staticmethod
    def _sma(data: list, period: int) -> list:
        if len(data) < period:
            avg = sum(data) / len(data) if data else 0
            return [avg] * len(data)

        result = []
        for i in range(len(data)):
            start = max(0, i - period + 1)
            window = data[start:i+1]
            result.append(sum(window) / len(window))

        return result

    # ══════════════════════════════════════════════════
    # COMPUTE ALL — Run full indicator stack on candles
    # ══════════════════════════════════════════════════
    @staticmethod
    def compute_all(candles: list) -> dict:
        """
        Takes a list of OHLC candles, returns all indicators.
        Used for each timeframe independently.
        """
        if not candles or len(candles) < 2:
            return Indicators._empty_indicators()

        closes = [c["close"] for c in candles]
        highs = [c["high"] for c in candles]
        lows = [c["low"] for c in candles]

        return {
            "ema_fast":     Indicators.ema(closes, IND["ema_fast"]),
            "ema_slow":     Indicators.ema(closes, IND["ema_slow"]),
            "ema_micro":    Indicators.ema(closes, IND["ema_micro"]),
            "rsi":          Indicators.rsi(closes, IND["rsi_period"]),
            "macd":         Indicators.macd(closes),
            "adx":          Indicators.adx(highs, lows, closes, IND["adx_period"]),
            "stoch_rsi":    Indicators.stoch_rsi(closes),
            "bollinger":    Indicators.bollinger(closes),
            "closes":       closes,
            "highs":        highs,
            "lows":         lows,
        }

    @staticmethod
    def compute_tick_analysis(prices: list) -> dict:
        """Run all tick-level analysis on raw price array"""
        return {
            "tmi":          Indicators.tick_momentum_index(prices),
            "streaks":      Indicators.count_streaks(prices),
            "acceleration": Indicators.tick_acceleration(prices),
            "last_price":   prices[-1] if prices else 0,
            "tick_count":   len(prices),
        }

    @staticmethod
    def _empty_indicators() -> dict:
        return {
            "ema_fast": [0], "ema_slow": [0], "ema_micro": [0],
            "rsi": [50], "macd": {"macd": [0], "signal": [0], "histogram": [0]},
            "adx": [0], "stoch_rsi": {"k": [50], "d": [50]},
            "bollinger": {"upper": [0], "middle": [0], "lower": [0]},
            "closes": [0], "highs": [0], "lows": [0],
        }