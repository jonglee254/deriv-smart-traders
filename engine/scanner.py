"""
═══════════════════════════════════════════════════════════════
  VOLATILITY SCANNER — Continuously ranks all indices
  by opportunity quality. Never misses a beat.
═══════════════════════════════════════════════════════════════
"""
import asyncio
import time
from config import VOLATILITY_INDICES, MIN_COMPOSITE_SCORE
from engine.analyzer import MarketAnalyzer
from engine.filters import SmartFilterEngine, TradeSignal


class IndexScore:
    """Score result for a single index"""

    def __init__(self, symbol: str):
        self.symbol = symbol
        self.name = VOLATILITY_INDICES.get(symbol, {}).get("name", symbol)
        self.score = 0.0
        self.direction = None
        self.signal = None
        self.status = "IDLE"         # IDLE, NO_TREND, WATCHING, READY, TRADING
        self.tmi = 50.0
        self.max_ticks = 0
        self.last_scan = 0
        self.gate_scores = {}

    def to_dict(self) -> dict:
        return {
            "symbol": self.symbol,
            "name": self.name,
            "score": round(self.score, 1),
            "direction": self.direction,
            "status": self.status,
            "tmi": round(self.tmi, 1),
            "max_ticks": self.max_ticks,
            "gate_scores": self.gate_scores,
            "last_scan": self.last_scan,
        }


class VolatilityScanner:
    """
    Continuously evaluates all 10 volatility indices.
    Ranks them and identifies the best trading opportunities.
    """

    def __init__(self, analyzer: MarketAnalyzer, filter_engine: SmartFilterEngine):
        self.analyzer = analyzer
        self.filters = filter_engine

        # Scores for all indices
        self.scores = {}
        for symbol in VOLATILITY_INDICES:
            self.scores[symbol] = IndexScore(symbol)

        # Currently trading symbols (to avoid duplicate entries)
        self.trading_symbols = set()

        # Scanner settings
        self.scan_interval = 0.5     # seconds between full scans
        self.max_simultaneous = 2    # max indices trading at once
        self.min_score_to_trade = MIN_COMPOSITE_SCORE
        self.running = False

        # Last full scan results
        self.ranked_list = []
        self.last_scan_time = 0

    # ══════════════════════════════════════════════════
    # SINGLE INDEX SCAN — Called on every tick
    # ══════════════════════════════════════════════════
    def scan_index(self, symbol: str) -> IndexScore:
        """
        Evaluate a single index. Called from tick handler
        for real-time updates.
        """
        market = self.analyzer.get_market(symbol)
        if not market or not market.initialized:
            score_obj = self.scores[symbol]
            score_obj.status = "IDLE"
            score_obj.score = 0
            return score_obj

        # Run through the filter engine
        signal = self.filters.evaluate(market)

        score_obj = self.scores[symbol]
        score_obj.last_scan = time.time()
        score_obj.tmi = market.tick_analysis.get("tmi", 50)

        if signal is None:
            # No trade signal
            # Determine why — check if there's any trend at all
            ind_5m = market.indicators.get("candles_5m", {})
            adx = ind_5m.get("adx", [0])[-1] if ind_5m.get("adx") else 0

            if adx < 20:
                score_obj.status = "NO_TREND"
            else:
                score_obj.status = "WATCHING"

            score_obj.score = 0
            score_obj.direction = None
            score_obj.signal = None
            score_obj.max_ticks = 0
            score_obj.gate_scores = {}
        else:
            score_obj.score = signal.composite_score
            score_obj.direction = signal.direction
            score_obj.signal = signal
            score_obj.max_ticks = signal.tick_count
            score_obj.gate_scores = signal.gate_scores

            if symbol in self.trading_symbols:
                score_obj.status = "TRADING"
            elif signal.composite_score >= self.min_score_to_trade:
                score_obj.status = "READY"
            else:
                score_obj.status = "WATCHING"

        return score_obj

    # ══════════════════════════════════════════════════
    # FULL SCAN — All indices at once
    # ══════════════════════════════════════════════════
    def full_scan(self) -> list:
        """
        Scan all indices and return ranked list.
        Called periodically by the scanner loop.
        """
        results = []

        for symbol in VOLATILITY_INDICES:
            score = self.scan_index(symbol)
            results.append(score)

        # Sort by score descending
        results.sort(key=lambda x: x.score, reverse=True)
        self.ranked_list = results
        self.last_scan_time = time.time()

        return results

    # ══════════════════════════════════════════════════
    # GET BEST OPPORTUNITIES
    # ══════════════════════════════════════════════════
    def get_best_signals(self, max_count: int = None) -> list:
        """
        Returns the top trade-ready signals that aren't
        already being traded.
        """
        if max_count is None:
            max_count = self.max_simultaneous

        available_slots = max_count - len(self.trading_symbols)

        if available_slots <= 0:
            return []

        signals = []
        for score_obj in self.ranked_list:
            if len(signals) >= available_slots:
                break

            if score_obj.score < self.min_score_to_trade:
                break  # Rest will be lower

            if score_obj.symbol in self.trading_symbols:
                continue  # Already trading this index

            if score_obj.signal is not None:
                signals.append(score_obj.signal)

        return signals

    # ══════════════════════════════════════════════════
    # TRADE TRACKING
    # ══════════════════════════════════════════════════
    def mark_trading(self, symbol: str):
        """Mark an index as currently being traded"""
        self.trading_symbols.add(symbol)
        if symbol in self.scores:
            self.scores[symbol].status = "TRADING"

    def mark_available(self, symbol: str):
        """Mark an index as available again after trade closes"""
        self.trading_symbols.discard(symbol)
        if symbol in self.scores:
            self.scores[symbol].status = "WATCHING"

    # ══════════════════════════════════════════════════
    # BACKGROUND SCANNER LOOP
    # ══════════════════════════════════════════════════
    async def run_loop(self, trade_callback=None):
        """
        Continuous scanner loop.
        trade_callback: async function(signal) called when
        a high-quality opportunity is found.
        """
        self.running = True
        print("🔍 Scanner loop started — monitoring all indices")

        while self.running:
            try:
                # Full scan
                self.full_scan()

                # Get actionable signals
                if trade_callback:
                    signals = self.get_best_signals()
                    for signal in signals:
                        asyncio.create_task(trade_callback(signal))

                await asyncio.sleep(self.scan_interval)

            except Exception as e:
                print(f"⚠️ Scanner error: {e}")
                await asyncio.sleep(1)

    def stop(self):
        self.running = False

    # ══════════════════════════════════════════════════
    # DASHBOARD DATA
    # ══════════════════════════════════════════════════
    def get_scanner_data(self) -> list:
        """Returns all index scores for dashboard display"""
        return [s.to_dict() for s in self.ranked_list] if self.ranked_list \
            else [self.scores[s].to_dict() for s in VOLATILITY_INDICES]

    def get_active_count(self) -> int:
        return len(self.trading_symbols)

    def get_ready_count(self) -> int:
        return sum(1 for s in self.scores.values() if s.status == "READY")