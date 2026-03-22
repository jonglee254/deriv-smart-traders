"""
═══════════════════════════════════════════════════════════════
  MARKET ANALYZER — Multi-Timeframe + Pattern Detection
  Processes raw data into actionable market intelligence.
═══════════════════════════════════════════════════════════════
"""
import asyncio
import time
from config import (
    VOLATILITY_INDICES, DATA, PATTERNS, INDICATORS as IND,
    INDEX_OVERRIDES
)
from engine.indicators import Indicators


class MarketData:
    """Holds all data and indicators for a single volatility index"""

    def __init__(self, symbol: str):
        self.symbol = symbol
        self.config = VOLATILITY_INDICES.get(symbol, {})
        self.overrides = INDEX_OVERRIDES.get(symbol, {})

        # Raw tick buffer
        self.raw_ticks = []

        # Candle buffers per timeframe
        self.candles = {
            "candles_1m": [],
            "candles_2m": [],
            "candles_3m": [],
            "candles_5m": [],
        }

        # Computed indicators per timeframe
        self.indicators = {
            "candles_1m": {},
            "candles_2m": {},
            "candles_3m": {},
            "candles_5m": {},
        }

        # Tick-level analysis
        self.tick_analysis = {
            "tmi": 50.0,
            "streaks": {},
            "acceleration": 0.0,
            "last_price": 0.0,
            "tick_count": 0,
        }

        # Detected patterns
        self.patterns = {
            "retest_bullish": {"detected": False, "retrace_ratio": 0},
            "retest_bearish": {"detected": False, "retrace_ratio": 0},
        }

        # Current building candle ticks (for building candles from ticks)
        self._candle_ticks = {60: [], 120: [], 180: [], 300: []}
        self._candle_open_time = {60: 0, 120: 0, 180: 0, 300: 0}

        # Metadata
        self.last_update = 0
        self.initialized = False


class MarketAnalyzer:
    """
    Central analysis engine. Manages data for ALL indices,
    processes ticks, updates indicators, detects patterns.
    """

    GRAN_MAP = {
        60:  "candles_1m",
        120: "candles_2m",
        180: "candles_3m",
        300: "candles_5m",
    }

    def __init__(self, api):
        self.api = api
        self.markets = {}
        self._buffer_size = DATA["tick_buffer_size"]
        self._candle_size = DATA["candle_buffer_size"]

        # Initialize market data objects
        for symbol in VOLATILITY_INDICES:
            self.markets[symbol] = MarketData(symbol)

    # ══════════════════════════════════════════════════
    # COLD START — Backfill all data on startup
    # ══════════════════════════════════════════════════
    async def cold_start(self):
        """Pull historical data for all indices simultaneously"""
        print("📥 Cold start: loading historical data...")

        tasks = []
        for symbol in VOLATILITY_INDICES:
            tasks.append(self._backfill_symbol(symbol))

        results = await asyncio.gather(*tasks, return_exceptions=True)

        success = 0
        for i, symbol in enumerate(VOLATILITY_INDICES):
            if isinstance(results[i], Exception):
                print(f"  ⚠️ {symbol}: backfill failed — {results[i]}")
            else:
                success += 1

        print(f"✅ Cold start complete: {success}/{len(VOLATILITY_INDICES)} indices loaded")

    async def _backfill_symbol(self, symbol: str):
        """Backfill one symbol with ticks + all timeframe candles"""
        market = self.markets[symbol]

        # Pull raw ticks
        ticks = await self.api.get_ticks_history(symbol, self._buffer_size)
        if ticks:
            market.raw_ticks = ticks
            prices = [t["price"] for t in ticks]
            market.tick_analysis = Indicators.compute_tick_analysis(prices)

        # Pull candles for each timeframe
        for gran, key in self.GRAN_MAP.items():
            candles = await self.api.get_candles(symbol, gran, self._candle_size)
            if candles:
                market.candles[key] = candles
                market.indicators[key] = Indicators.compute_all(candles)

        # Detect patterns on 2m candles
        self._detect_patterns(market)

        market.initialized = True
        market.last_update = time.time()

        name = VOLATILITY_INDICES[symbol]["name"]
        tick_count = len(market.raw_ticks)
        print(f"  ✅ {name}: {tick_count} ticks, all timeframes loaded")

    # ══════════════════════════════════════════════════
    # LIVE TICK PROCESSING
    # ══════════════════════════════════════════════════
    def process_tick(self, symbol: str, tick_data: dict):
        """
        Called for every incoming tick. Updates everything incrementally.
        Returns True if any candle closed (triggering deeper analysis).
        """
        market = self.markets.get(symbol)
        if not market:
            return False

        price = float(tick_data.get("quote", tick_data.get("ask", 0)))
        epoch = int(tick_data.get("epoch", time.time()))

        # 1. Append to tick buffer
        market.raw_ticks.append({"price": price, "time": epoch})
        if len(market.raw_ticks) > self._buffer_size:
            market.raw_ticks = market.raw_ticks[-self._buffer_size:]

        # 2. Update tick-level analysis (fast incremental)
        prices = [t["price"] for t in market.raw_ticks]
        market.tick_analysis = Indicators.compute_tick_analysis(prices)

        # 3. Check if any candle timeframe closed
        candle_closed = False
        for gran, key in self.GRAN_MAP.items():
            if self._update_candle(market, gran, key, price, epoch):
                candle_closed = True

        # 4. If a candle closed, re-detect patterns
        if candle_closed:
            self._detect_patterns(market)

        market.last_update = time.time()
        return candle_closed

    def _update_candle(self, market: MarketData, granularity: int,
                       key: str, price: float, epoch: int) -> bool:
        """
        Build candles from ticks. Returns True if a candle just closed.
        """
        candle_ticks = market._candle_ticks[granularity]
        open_time = market._candle_open_time[granularity]

        # Determine candle boundaries
        candle_start = epoch - (epoch % granularity)

        if open_time == 0:
            # First tick — start tracking
            market._candle_open_time[granularity] = candle_start
            candle_ticks.append(price)
            return False

        if candle_start == open_time:
            # Same candle — just accumulate
            candle_ticks.append(price)
            return False

        # New candle boundary crossed — close the previous candle
        if candle_ticks:
            new_candle = {
                "open":  candle_ticks[0],
                "high":  max(candle_ticks),
                "low":   min(candle_ticks),
                "close": candle_ticks[-1],
                "time":  open_time,
            }

            market.candles[key].append(new_candle)
            if len(market.candles[key]) > self._candle_size:
                market.candles[key] = market.candles[key][-self._candle_size:]

            # Recompute indicators for this timeframe
            market.indicators[key] = Indicators.compute_all(market.candles[key])

        # Reset for new candle
        market._candle_ticks[granularity] = [price]
        market._candle_open_time[granularity] = candle_start

        return True

    # ══════════════════════════════════════════════════
    # PATTERN DETECTION
    # ══════════════════════════════════════════════════
    def _detect_patterns(self, market: MarketData):
        """
        Detects Rise-Rise-Rise / Fall-Fall retest patterns on 2m candles.
        Also checks 1m for finer pattern detection.
        """
        candles_2m = market.candles.get("candles_2m", [])
        market.patterns["retest_bullish"] = self._detect_rrr_ff(candles_2m, "bullish")
        market.patterns["retest_bearish"] = self._detect_rrr_ff(candles_2m, "bearish")

    def _detect_rrr_ff(self, candles: list, direction: str) -> dict:
        """
        Bullish: Rise-Rise-Rise then Fall-Fall (pullback), then bounce
        Bearish: Fall-Fall-Fall then Rise-Rise (pullback), then drop

        Returns detection status and retrace depth.
        """
        rise_count = PATTERNS["retest_rise_count"]
        fall_count = PATTERNS["retest_fall_count"]
        max_retrace = PATTERNS["max_retrace_ratio"]

        min_needed = rise_count + fall_count + 1
        if len(candles) < min_needed:
            return {"detected": False, "retrace_ratio": 0, "strength": 0}

        recent = candles[-(min_needed + 2):]  # Extra buffer for flexibility

        if direction == "bullish":
            return self._find_bullish_retest(recent, rise_count,
                                              fall_count, max_retrace)
        else:
            return self._find_bearish_retest(recent, rise_count,
                                              fall_count, max_retrace)

    def _find_bullish_retest(self, candles: list, rises: int,
                              falls: int, max_retrace: float) -> dict:
        """
        Look for N consecutive rising candles followed by
        up to M falling candles that don't retrace too deep.
        """
        n = len(candles)

        for start in range(n - rises - 1, -1, -1):
            # Check for consecutive rises
            rise_valid = True
            for i in range(start, min(start + rises, n)):
                if i < 0 or i >= n:
                    rise_valid = False
                    break
                if candles[i]["close"] <= candles[i]["open"]:
                    rise_valid = False
                    break

            if not rise_valid:
                continue

            # Found rises — now look for pullback after them
            pull_start = start + rises
            if pull_start >= n:
                continue

            # Measure the impulse move
            impulse_low = candles[start]["low"]
            impulse_high = max(c["high"] for c in candles[start:pull_start])
            impulse_range = impulse_high - impulse_low

            if impulse_range <= 0:
                continue

            # Count falling candles (pullback)
            pull_count = 0
            pull_low = impulse_high

            for i in range(pull_start, min(pull_start + falls + 1, n)):
                if candles[i]["close"] < candles[i]["open"]:
                    pull_count += 1
                    pull_low = min(pull_low, candles[i]["low"])
                else:
                    break  # Pullback ended with a green candle

            if pull_count == 0:
                continue  # No pullback yet

            # Calculate retrace ratio
            retrace = (impulse_high - pull_low) / impulse_range

            if retrace > max_retrace:
                continue  # Too deep

            # Check if pullback held and last candle shows bounce
            last_candle = candles[min(pull_start + pull_count, n - 1)]
            bouncing = last_candle["close"] > last_candle["open"]

            # Calculate strength
            strength = 0
            if retrace <= 0.382:
                strength = 90  # Shallow pullback = very strong
            elif retrace <= 0.5:
                strength = 70
            elif retrace <= 0.618:
                strength = 50

            if bouncing:
                strength += 10

            # Pull count within limits?
            if pull_count <= falls:
                return {
                    "detected": True,
                    "retrace_ratio": round(retrace, 4),
                    "strength": min(100, strength),
                    "impulse_high": impulse_high,
                    "pullback_low": pull_low,
                    "bouncing": bouncing,
                    "pull_candles": pull_count,
                }

        return {"detected": False, "retrace_ratio": 0, "strength": 0}

    def _find_bearish_retest(self, candles: list, falls: int,
                              rises: int, max_retrace: float) -> dict:
        """Mirror of bullish — Fall-Fall-Fall then Rise-Rise pullback"""
        n = len(candles)

        for start in range(n - falls - 1, -1, -1):
            # Check for consecutive falls
            fall_valid = True
            for i in range(start, min(start + falls, n)):
                if i < 0 or i >= n:
                    fall_valid = False
                    break
                if candles[i]["close"] >= candles[i]["open"]:
                    fall_valid = False
                    break

            if not fall_valid:
                continue

            pull_start = start + falls
            if pull_start >= n:
                continue

            impulse_high = candles[start]["high"]
            impulse_low = min(c["low"] for c in candles[start:pull_start])
            impulse_range = impulse_high - impulse_low

            if impulse_range <= 0:
                continue

            pull_count = 0
            pull_high = impulse_low

            for i in range(pull_start, min(pull_start + rises + 1, n)):
                if candles[i]["close"] > candles[i]["open"]:
                    pull_count += 1
                    pull_high = max(pull_high, candles[i]["high"])
                else:
                    break

            if pull_count == 0:
                continue

            retrace = (pull_high - impulse_low) / impulse_range

            if retrace > max_retrace:
                continue

            last_candle = candles[min(pull_start + pull_count, n - 1)]
            dropping = last_candle["close"] < last_candle["open"]

            strength = 0
            if retrace <= 0.382:
                strength = 90
            elif retrace <= 0.5:
                strength = 70
            elif retrace <= 0.618:
                strength = 50

            if dropping:
                strength += 10

            if pull_count <= rises:
                return {
                    "detected": True,
                    "retrace_ratio": round(retrace, 4),
                    "strength": min(100, strength),
                    "impulse_low": impulse_low,
                    "pullback_high": pull_high,
                    "dropping": dropping,
                    "pull_candles": pull_count,
                }

        return {"detected": False, "retrace_ratio": 0, "strength": 0}

    # ══════════════════════════════════════════════════
    # DATA ACCESS — Used by filters and scanner
    # ══════════════════════════════════════════════════
    def get_market(self, symbol: str) -> MarketData:
        return self.markets.get(symbol)

    def get_all_markets(self) -> dict:
        return self.markets

    def is_ready(self, symbol: str) -> bool:
        m = self.markets.get(symbol)
        return m is not None and m.initialized and len(m.raw_ticks) > 50

    def get_snapshot(self, symbol: str) -> dict:
        """Dashboard-ready snapshot of a single index"""
        m = self.markets.get(symbol)
        if not m:
            return {}

        ta = m.tick_analysis
        last_price = ta.get("last_price", 0)

        # Get last indicator values per timeframe
        ind_summary = {}
        for tf_key in ["candles_1m", "candles_2m", "candles_3m", "candles_5m"]:
            ind = m.indicators.get(tf_key, {})
            if not ind or not ind.get("rsi"):
                continue
            ind_summary[tf_key] = {
                "ema_fast":  ind["ema_fast"][-1] if ind.get("ema_fast") else 0,
                "ema_slow":  ind["ema_slow"][-1] if ind.get("ema_slow") else 0,
                "rsi":       ind["rsi"][-1] if ind.get("rsi") else 50,
                "macd_hist": ind["macd"]["histogram"][-1] if ind.get("macd") else 0,
                "adx":       ind["adx"][-1] if ind.get("adx") else 0,
                "stoch_k":   ind["stoch_rsi"]["k"][-1] if ind.get("stoch_rsi") else 50,
                "stoch_d":   ind["stoch_rsi"]["d"][-1] if ind.get("stoch_rsi") else 50,
            }

        return {
            "symbol": symbol,
            "name": VOLATILITY_INDICES.get(symbol, {}).get("name", symbol),
            "price": last_price,
            "tmi": ta.get("tmi", 50),
            "acceleration": ta.get("acceleration", 0),
            "streaks": ta.get("streaks", {}),
            "patterns": m.patterns,
            "indicators": ind_summary,
            "initialized": m.initialized,
            "last_update": m.last_update,
        }