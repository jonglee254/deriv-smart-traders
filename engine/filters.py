"""
═══════════════════════════════════════════════════════════════
  SMART CASCADING FILTER ENGINE
  Gates filter each other — no waiting for full alignment.
  Speed first, accuracy through cross-validation.
═══════════════════════════════════════════════════════════════
"""
from config import (
    INDICATORS as IND, GATE_WEIGHTS, TIMEFRAME_WEIGHTS,
    MIN_COMPOSITE_SCORE, HIGH_CONFIDENCE_SCORE,
    TICK_COUNT_THRESHOLDS, INDEX_OVERRIDES, ACCOUNT_TIERS
)
from engine.analyzer import MarketData


class TradeSignal:
    """Output of the filter engine"""
    def __init__(self):
        self.symbol = ""
        self.direction = None          # "UP" or "DOWN"
        self.contract_type = None      # "ONLY_UPS", "ONLY_DOWNS", "HIGH", "LOW"
        self.tick_count = 0            # 2-5 for Only Ups/Downs
        self.barrier = None            # For High/Low contracts
        self.composite_score = 0
        self.gate_scores = {}
        self.indicator_values = {}
        self.reason = ""
        self.timestamp = 0

    def to_dict(self) -> dict:
        return {
            "symbol": self.symbol,
            "direction": self.direction,
            "contract_type": self.contract_type,
            "tick_count": self.tick_count,
            "barrier": self.barrier,
            "composite_score": round(self.composite_score, 1),
            "gate_scores": self.gate_scores,
            "indicator_values": self.indicator_values,
            "reason": self.reason,
        }


class SmartFilterEngine:
    """
    Four-gate cascading filter with weighted scoring.
    Gate 1 is a hard kill. Gates 2-4 are soft — they score
    and compensate for each other through the composite.
    """

    def __init__(self, account_tier: str = "SMALL"):
        self.account_tier = account_tier
        self.max_ticks_allowed = ACCOUNT_TIERS.get(
            account_tier, {}
        ).get("max_ticks", 3)

        # Adaptive weights — start with defaults
        self.weights = dict(GATE_WEIGHTS)
        self.tf_weights = dict(TIMEFRAME_WEIGHTS)

    def set_account_tier(self, tier: str):
        self.account_tier = tier
        self.max_ticks_allowed = ACCOUNT_TIERS.get(
            tier, {}
        ).get("max_ticks", 3)

    # ══════════════════════════════════════════════════
    # MASTER EVALUATE — Single entry point
    # ══════════════════════════════════════════════════
    def evaluate(self, market: MarketData) -> TradeSignal:
        """
        Run all gates on a market, produce a TradeSignal or None.
        This is called on every tick for every active index.
        """
        if not market.initialized:
            return None

        overrides = market.overrides
        min_composite = overrides.get("min_composite", MIN_COMPOSITE_SCORE)

        # Adapt weights to market regime before evaluation
        self._adapt_weights(market)

        # ═══ GATE 1: TREND EXISTS (Hard Kill) ═══
        g1_pass, g1_score, direction, g1_details = self._gate_1_trend(market)

        if not g1_pass:
            return None

        # ═══ GATE 2: MOMENTUM (Soft) ═══
        g2_pass, g2_score, g2_details = self._gate_2_momentum(
            market, direction
        )

        # ═══ GATE 3: PATTERN + TIMING (Soft) ═══
        g3_pass, g3_score, g3_details = self._gate_3_pattern(
            market, direction
        )

        # ═══ GATE 4: TICK PURITY (Soft) ═══
        g4_pass, g4_score, max_ticks, g4_details = self._gate_4_ticks(
            market, direction
        )

        # ═══ COMPOSITE SCORE ═══
        composite = (
            g1_score * self.weights["trend"] +
            g2_score * self.weights["momentum"] +
            g3_score * self.weights["pattern"] +
            g4_score * self.weights["tick_purity"]
        )

        # ═══ SMART CROSS-VALIDATION BONUSES ═══
        # Strong trend + pattern = high conviction setup
        if g1_score > 75 and g3_score > 35:
            composite += 5

        # Strong momentum + clean ticks = ideal for high tick count
        if g2_score > 65 and g4_score > 70:
            composite += 5

        # Pattern retest detected + momentum confirming = textbook entry
        if g3_score > 60 and g2_score > 55:
            composite += 4

        # All 3 soft gates above 50 = everything agrees loosely
        if g2_score > 50 and g3_score > 50 and g4_score > 50:
            composite += 3

        composite = min(100, composite)

        # ═══ FINAL DECISION ═══
        if composite < min_composite:
            return None

        # ═══ BUILD SIGNAL ═══
        signal = TradeSignal()
        signal.symbol = market.symbol
        signal.direction = direction
        signal.composite_score = composite

        signal.gate_scores = {
            "trend":       round(g1_score, 1),
            "momentum":    round(g2_score, 1),
            "pattern":     round(g3_score, 1),
            "tick_purity": round(g4_score, 1),
        }

        signal.indicator_values = {
            **g1_details, **g2_details, **g3_details, **g4_details
        }

        # ── Contract type selection ──
        effective_max = min(max_ticks, self.max_ticks_allowed)

        if g4_pass and effective_max >= 2:
            # Only Ups/Downs viable
            if direction == "UP":
                signal.contract_type = "ONLY_UPS"
            else:
                signal.contract_type = "ONLY_DOWNS"

            # Dynamic tick count based on composite + purity
            if composite >= HIGH_CONFIDENCE_SCORE and effective_max >= 4:
                signal.tick_count = min(effective_max, 5)
            elif composite >= 72 and effective_max >= 3:
                signal.tick_count = 3
            else:
                signal.tick_count = 2

            signal.reason = (f"{signal.contract_type} {signal.tick_count}T | "
                           f"Score: {composite:.0f}")
        else:
            # Fall back to High/Low
            signal.contract_type = "HIGH" if direction == "UP" else "LOW"
            signal.barrier = self._calculate_barrier(composite, market)
            signal.reason = (f"{signal.contract_type} B:{signal.barrier} | "
                           f"Score: {composite:.0f}")

        return signal

    # ══════════════════════════════════════════════════
    # GATE 1: TREND EXISTS
    # ══════════════════════════════════════════════════
    def _gate_1_trend(self, market: MarketData):
        """
        ADX + EMA alignment across timeframes.
        HARD GATE — if no trend, everything stops.
        """
        direction_votes = {"UP": 0.0, "DOWN": 0.0}
        total_weight = 0.0
        adx_primary = 0.0
        details = {}

        adx_threshold = market.overrides.get(
            "adx_trend_threshold", IND["adx_trend_threshold"]
        )

        for tf_key, weight in self.tf_weights.items():
            ind = market.indicators.get(tf_key, {})
            if not ind or not ind.get("ema_fast") or not ind.get("adx"):
                continue

            ema_f = ind["ema_fast"][-1]
            ema_s = ind["ema_slow"][-1]
            adx_val = ind["adx"][-1]

            if ema_f > ema_s:
                direction_votes["UP"] += weight
            elif ema_f < ema_s:
                direction_votes["DOWN"] += weight

            total_weight += weight

            if tf_key == "candles_5m":
                adx_primary = adx_val
                details["adx_5m"] = round(adx_val, 1)

            if tf_key == "candles_2m":
                details["ema_fast_2m"] = round(ema_f, 2)
                details["ema_slow_2m"] = round(ema_s, 2)

        # HARD KILL: No trend
        if adx_primary < adx_threshold:
            return False, 0, None, {"adx_5m": round(adx_primary, 1)}

        if total_weight == 0:
            return False, 0, None, {}

        up_ratio = direction_votes["UP"] / total_weight
        down_ratio = direction_votes["DOWN"] / total_weight

        if up_ratio > 0.55:
            direction = "UP"
            alignment = up_ratio
        elif down_ratio > 0.55:
            direction = "DOWN"
            alignment = down_ratio
        else:
            return False, 0, None, details

        # Score
        adx_points = min(50, (adx_primary / 50) * 50)
        align_points = ((alignment - 0.55) / 0.45) * 50

        score = min(100, adx_points + align_points)
        details["direction"] = direction
        details["alignment"] = round(alignment, 3)

        return True, score, direction, details

    # ══════════════════════════════════════════════════
    # GATE 2: MOMENTUM
    # ══════════════════════════════════════════════════
    def _gate_2_momentum(self, market: MarketData, direction: str):
        """
        RSI + TMI cross-validated.
        Each compensates for the other's weakness.
        """
        details = {}

        # Get RSI from 2m timeframe (entry zone)
        ind_2m = market.indicators.get("candles_2m", {})
        rsi = ind_2m.get("rsi", [50])[-1] if ind_2m.get("rsi") else 50
        tmi = market.tick_analysis.get("tmi", 50)

        tmi_minimum = market.overrides.get("tmi_minimum", IND["tmi_minimum"])

        details["rsi_2m"] = round(rsi, 1)
        details["tmi"] = round(tmi, 1)

        rsi_points = 0
        tmi_points = 0

        if direction == "UP":
            # RSI assessment (0-50)
            if rsi > IND["rsi_overbought"]:
                rsi_points = 15  # Overbought penalty, not kill
            elif rsi > IND["rsi_bullish_zone"]:
                ratio = (rsi - IND["rsi_bullish_zone"]) / (
                    IND["rsi_overbought"] - IND["rsi_bullish_zone"]
                )
                rsi_points = 30 + ratio * 20
            else:
                rsi_points = max(0, (rsi / IND["rsi_bullish_zone"]) * 20)

            # TMI assessment (0-50)
            if tmi > IND["tmi_strong"]:
                ratio = (tmi - IND["tmi_strong"]) / (100 - IND["tmi_strong"])
                tmi_points = 35 + ratio * 15
            elif tmi > tmi_minimum:
                ratio = (tmi - tmi_minimum) / (IND["tmi_strong"] - tmi_minimum)
                tmi_points = 20 + ratio * 15
            else:
                tmi_points = max(0, (tmi / tmi_minimum) * 15)

            # Cross-validation bonuses
            if rsi > IND["rsi_overbought"] and tmi > 75:
                rsi_points += 10  # Momentum override
            if 50 < rsi < 60 and tmi > tmi_minimum:
                tmi_points += 8   # Early trend bonus

        else:  # DOWN
            inv_rsi = 100 - rsi
            inv_tmi = 100 - tmi

            if rsi < IND["rsi_oversold"]:
                rsi_points = 15
            elif rsi < IND["rsi_bearish_zone"]:
                ratio = (IND["rsi_bearish_zone"] - rsi) / (
                    IND["rsi_bearish_zone"] - IND["rsi_oversold"]
                )
                rsi_points = 30 + ratio * 20
            else:
                rsi_points = max(0, (inv_rsi / (100 - IND["rsi_bearish_zone"])) * 20)

            if inv_tmi > IND["tmi_strong"]:
                ratio = (inv_tmi - IND["tmi_strong"]) / (100 - IND["tmi_strong"])
                tmi_points = 35 + ratio * 15
            elif inv_tmi > tmi_minimum:
                ratio = (inv_tmi - tmi_minimum) / (IND["tmi_strong"] - tmi_minimum)
                tmi_points = 20 + ratio * 15
            else:
                tmi_points = max(0, (inv_tmi / tmi_minimum) * 15)

            if rsi < IND["rsi_oversold"] and inv_tmi > 75:
                rsi_points += 10
            if 40 < rsi < 50 and inv_tmi > tmi_minimum:
                tmi_points += 8

        score = min(100, rsi_points + tmi_points)
        passed = score >= 25  # Very soft gate

        return passed, score, details

    # ══════════════════════════════════════════════════
    # GATE 3: PATTERN + TIMING
    # ══════════════════════════════════════════════════
    def _gate_3_pattern(self, market: MarketData, direction: str):
        """
        Retest pattern + Stochastic RSI + MACD histogram.
        Any TWO of three scoring well = solid entry.
        """
        details = {}
        score = 0

        # ── Retest Pattern (0-40 points) ──
        pattern_key = "retest_bullish" if direction == "UP" else "retest_bearish"
        pattern = market.patterns.get(pattern_key, {})

        if pattern.get("detected", False):
            retrace = pattern.get("retrace_ratio", 1.0)
            if retrace <= 0.382:
                score += 40
                details["pattern"] = "SHALLOW_RETEST"
            elif retrace <= 0.5:
                score += 32
                details["pattern"] = "MODERATE_RETEST"
            elif retrace <= 0.618:
                score += 22
                details["pattern"] = "DEEP_RETEST"
            details["retrace"] = round(retrace, 4)
        else:
            details["pattern"] = "NONE"

        # ── Stochastic RSI (0-30 points) ──
        ind_2m = market.indicators.get("candles_2m", {})
        stoch = ind_2m.get("stoch_rsi", {"k": [50], "d": [50]})
        stoch_k = stoch["k"][-1] if stoch.get("k") else 50
        stoch_d = stoch["d"][-1] if stoch.get("d") else 50

        details["stoch_k"] = round(stoch_k, 1)
        details["stoch_d"] = round(stoch_d, 1)

        if direction == "UP":
            if stoch_k < IND["stoch_oversold"] and stoch_k > stoch_d:
                score += 30  # Perfect: oversold + crossing up
            elif stoch_k < 40 and stoch_k > stoch_d:
                score += 20
            elif stoch_k > stoch_d:
                score += 10
            elif stoch_k < 30:
                score += 8   # Very oversold — likely to bounce
        else:
            if stoch_k > IND["stoch_overbought"] and stoch_k < stoch_d:
                score += 30
            elif stoch_k > 60 and stoch_k < stoch_d:
                score += 20
            elif stoch_k < stoch_d:
                score += 10
            elif stoch_k > 70:
                score += 8

        # ── MACD Histogram (0-30 points) ──
        macd_data = ind_2m.get("macd", {})
        histogram = macd_data.get("histogram", [0])

        h_current = histogram[-1] if histogram else 0
        h_prev = histogram[-2] if len(histogram) > 1 else 0

        details["macd_hist"] = round(h_current, 6)

        if direction == "UP":
            if h_current > 0 and h_current > h_prev:
                score += 30  # Positive and growing
            elif h_current > 0:
                score += 18  # Positive but slowing
            elif h_current > h_prev:
                score += 12  # Negative but improving
            # Bonus: MACD just crossed above signal
            macd_line = macd_data.get("macd", [0])
            sig_line = macd_data.get("signal", [0])
            if len(macd_line) > 1 and len(sig_line) > 1:
                if (macd_line[-1] > sig_line[-1] and
                        macd_line[-2] <= sig_line[-2]):
                    score += 8  # Fresh cross bonus
                    details["macd_cross"] = "BULLISH"
        else:
            if h_current < 0 and h_current < h_prev:
                score += 30
            elif h_current < 0:
                score += 18
            elif h_current < h_prev:
                score += 12
            macd_line = macd_data.get("macd", [0])
            sig_line = macd_data.get("signal", [0])
            if len(macd_line) > 1 and len(sig_line) > 1:
                if (macd_line[-1] < sig_line[-1] and
                        macd_line[-2] >= sig_line[-2]):
                    score += 8
                    details["macd_cross"] = "BEARISH"

        passed = score >= 20
        return passed, min(100, score), details

    # ══════════════════════════════════════════════════
    # GATE 4: TICK PURITY
    # ══════════════════════════════════════════════════
    def _gate_4_ticks(self, market: MarketData, direction: str):
        """
        Streak frequency + acceleration + TMI consistency.
        Determines if Only Ups/Downs is viable and max tick count.
        """
        ta = market.tick_analysis
        details = {}
        streaks = ta.get("streaks", {})
        accel = ta.get("acceleration", 0)
        tmi = ta.get("tmi", 50)

        dir_tmi = tmi if direction == "UP" else (100 - tmi)
        dir_key = "up_pct" if direction == "UP" else "down_pct"

        # ── Streak Frequency (0-40 points) ──
        streak_2 = streaks.get(2, {}).get(dir_key, 0)
        streak_3 = streaks.get(3, {}).get(dir_key, 0)

        streak_points = min(40, (streak_2 / 100) * 25 + (streak_3 / 100) * 15)
        details["streak_2_pct"] = round(streak_2, 1)
        details["streak_3_pct"] = round(streak_3, 1)

        # ── Acceleration (0-30 points) ──
        accel_points = 0
        if direction == "UP" and accel > 0:
            accel_points = min(30, accel * 300)
        elif direction == "DOWN" and accel < 0:
            accel_points = min(30, abs(accel) * 300)
        else:
            accel_points = max(0, 5 - abs(accel) * 100)  # Near-zero is OK

        details["acceleration"] = round(accel, 6)

        # ── TMI Consistency (0-30 points) ──
        tmi_points = max(0, ((dir_tmi - 50) / 50) * 30)
        details["dir_tmi"] = round(dir_tmi, 1)

        score = min(100, streak_points + accel_points + tmi_points)

        # ── Determine max safe tick count ──
        overrides = market.overrides
        thresholds = dict(TICK_COUNT_THRESHOLDS)

        # Apply per-index overrides
        if "tick_3_min" in overrides:
            thresholds[3] = overrides["tick_3_min"]
        if "tick_5_min" in overrides:
            thresholds[5] = overrides["tick_5_min"]

        max_ticks = 0
        for ticks in sorted(thresholds.keys(), reverse=True):
            if score >= thresholds[ticks]:
                max_ticks = ticks
                break

        details["max_ticks"] = max_ticks

        passed = max_ticks >= 2
        return passed, score, max_ticks, details

    # ══════════════════════════════════════════════════
    # BARRIER CALCULATION (High/Low contracts)
    # ══════════════════════════════════════════════════
    def _calculate_barrier(self, composite: float,
                           market: MarketData) -> str:
        """
        Higher confidence → tighter barrier (more payout, more risk)
        Lower confidence → wider barrier (less payout, safer)
        Recovery mode → widest barrier possible
        """
        noise = market.config.get("noise", 0.5)

        if composite >= 85:
            barrier_factor = 0.0005 * noise
        elif composite >= 75:
            barrier_factor = 0.001 * noise
        elif composite >= 65:
            barrier_factor = 0.002 * noise
        else:
            barrier_factor = 0.003 * noise

        # Get last price to calculate actual barrier
        last_price = market.tick_analysis.get("last_price", 0)
        if last_price == 0:
            return "+0.50"

        barrier_value = last_price * barrier_factor

        # Deriv expects barrier as offset string
        return f"+{barrier_value:.2f}"

    def get_recovery_barrier(self, market: MarketData) -> str:
        """Extra wide barrier for recovery trades — maximize win rate"""
        noise = market.config.get("noise", 0.5)
        last_price = market.tick_analysis.get("last_price", 0)
        if last_price == 0:
            return "+0.10"
        barrier_value = last_price * 0.0001 * noise
        return f"+{max(0.01, barrier_value):.2f}"

    # ══════════════════════════════════════════════════
    # ADAPTIVE WEIGHT SHIFTING
    # ══════════════════════════════════════════════════
    def _adapt_weights(self, market: MarketData):
        """
        Shift gate weights based on current market regime.
        Detected from the 5m ADX and pattern state.
        """
        ind_5m = market.indicators.get("candles_5m", {})
        adx_val = ind_5m.get("adx", [0])[-1] if ind_5m.get("adx") else 0

        has_pattern = (market.patterns["retest_bullish"]["detected"] or
                       market.patterns["retest_bearish"]["detected"])

        if adx_val > IND["adx_strong_trend"]:
            # Strong trend — trust direction and tick purity
            self.weights = {
                "trend": 0.35, "momentum": 0.20,
                "pattern": 0.15, "tick_purity": 0.30
            }
        elif has_pattern:
            # Pullback in trend — pattern is king
            self.weights = {
                "trend": 0.25, "momentum": 0.20,
                "pattern": 0.35, "tick_purity": 0.20
            }
        elif adx_val > IND["adx_trend_threshold"]:
            # Weak trend — need strong momentum confirmation
            self.weights = {
                "trend": 0.25, "momentum": 0.30,
                "pattern": 0.25, "tick_purity": 0.20
            }
        else:
            # Default
            self.weights = dict(GATE_WEIGHTS)