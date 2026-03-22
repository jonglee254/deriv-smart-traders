"""
═══════════════════════════════════════════════════════════════
  TRADE EXECUTOR — Handles contract creation, buying,
  result tracking for Only Ups/Downs + High/Low
═══════════════════════════════════════════════════════════════
"""
import asyncio
import time
from config import RISK


class TradeRecord:
    """Stores everything about a single trade"""

    def __init__(self):
        self.id = ""
        self.symbol = ""
        self.contract_type = ""
        self.direction = ""
        self.tick_count = 0
        self.barrier = None
        self.stake = 0.0
        self.potential_payout = 0.0
        self.payout_ratio = 0.0
        self.confidence = 0.0
        self.mode = ""
        self.gate_scores = {}
        self.entry_price = 0.0
        self.exit_price = 0.0
        self.profit = 0.0
        self.result = ""           # "WIN", "LOSS", "PENDING"
        self.open_time = 0
        self.close_time = 0
        self.contract_id = None
        self.buy_response = {}

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "symbol": self.symbol,
            "contract_type": self.contract_type,
            "direction": self.direction,
            "tick_count": self.tick_count,
            "barrier": self.barrier,
            "stake": self.stake,
            "potential_payout": round(self.potential_payout, 2),
            "payout_ratio": round(self.payout_ratio, 2),
            "confidence": round(self.confidence, 1),
            "mode": self.mode,
            "gate_scores": self.gate_scores,
            "entry_price": self.entry_price,
            "exit_price": self.exit_price,
            "profit": round(self.profit, 2),
            "result": self.result,
            "open_time": self.open_time,
            "close_time": self.close_time,
        }


class TradeExecutor:
    """
    Handles the full lifecycle of a trade:
    1. Build contract parameters
    2. Get proposal (price/payout)
    3. Execute buy
    4. Track result
    5. Report to risk engine
    """

    # Deriv contract type mapping
    CONTRACT_MAP = {
        "ONLY_UPS":   "RUNHIGH",
        "ONLY_DOWNS": "RUNLOW",
        "HIGH":       "CALL",
        "LOW":        "PUT",
    }

    def __init__(self, api, risk_engine, analyzer):
        self.api = api
        self.risk = risk_engine
        self.analyzer = analyzer

        # Active trades tracking
        self.active_trades = {}
        self.trade_history = []
        self.trade_counter = 0

        # Concurrency control
        self.max_concurrent = 2
        self._executing = False
        self._lock = asyncio.Lock()

        # Callbacks for dashboard and telegram
        self.on_trade_opened = None     # async callback(TradeRecord)
        self.on_trade_closed = None     # async callback(TradeRecord)
        self.on_recovery_start = None   # async callback(dict)
        self.on_compound_start = None   # async callback(dict)
        self.on_emergency = None        # async callback(str, float)

    # ══════════════════════════════════════════════════
    # EXECUTE TRADE — Main entry point
    # ══════════════════════════════════════════════════
    async def execute(self, signal, stake_info: dict) -> TradeRecord:
        """
        Takes a TradeSignal and stake calculation,
        executes the contract on Deriv.
        """
        async with self._lock:
            if self._executing:
                return None

            if len(self.active_trades) >= self.max_concurrent:
                return None

            self._executing = True

        try:
            trade = TradeRecord()
            self.trade_counter += 1
            trade.id = f"T{self.trade_counter:05d}"
            trade.symbol = signal.symbol
            trade.contract_type = signal.contract_type
            trade.direction = signal.direction
            trade.tick_count = signal.tick_count
            trade.barrier = signal.barrier
            trade.stake = stake_info["stake"]
            trade.confidence = signal.composite_score
            trade.mode = stake_info["mode"]
            trade.gate_scores = signal.gate_scores
            trade.open_time = time.time()
            trade.result = "PENDING"

            # Get current price
            market = self.analyzer.get_market(signal.symbol)
            if market:
                trade.entry_price = market.tick_analysis.get("last_price", 0)

            # Build contract parameters
            params = self._build_params(signal, stake_info)

            if not params:
                return None

            # Get proposal first to see payout
            proposal_resp = await self.api.get_proposal(params)

            if "error" in proposal_resp:
                err = proposal_resp["error"].get("message", "Unknown error")
                print(f"  ⚠️ Proposal error on {signal.symbol}: {err}")
                return None

            if "proposal" not in proposal_resp:
                print(f"  ⚠️ No proposal returned for {signal.symbol}")
                return None

            prop = proposal_resp["proposal"]
            trade.potential_payout = float(prop.get("payout", 0))
            trade.payout_ratio = (
                (trade.potential_payout - trade.stake) / trade.stake
                if trade.stake > 0 else 0
            )

            proposal_id = prop["id"]

            # Execute buy
            buy_resp = await self.api.buy(proposal_id, trade.stake)

            if "error" in buy_resp:
                err = buy_resp["error"].get("message", "Unknown error")
                print(f"  ⚠️ Buy error on {signal.symbol}: {err}")
                return None

            if "buy" not in buy_resp:
                print(f"  ⚠️ Buy failed for {signal.symbol}")
                return None

            buy_data = buy_resp["buy"]
            trade.contract_id = buy_data.get("contract_id")
            trade.buy_response = buy_data

            # Track as active
            self.active_trades[trade.contract_id] = trade

            # Update balance from buy response
            new_bal = float(buy_data.get("balance_after", self.api.balance))
            self.risk.update_balance(new_bal)

            print(f"  ⚡ TRADE {trade.id}: {trade.contract_type} "
                  f"{'↑' if trade.direction == 'UP' else '↓'} "
                  f"{trade.tick_count}T on {trade.symbol} | "
                  f"Stake: ${trade.stake:.2f} | "
                  f"Payout: ${trade.potential_payout:.2f}")

            # Notify callbacks
            if self.on_trade_opened:
                asyncio.create_task(self.on_trade_opened(trade))

            # Start monitoring the contract result
            asyncio.create_task(self._monitor_contract(trade))

            return trade

        except Exception as e:
            print(f"  ❌ Execution error: {e}")
            return None

        finally:
            self._executing = False

    # ══════════════════════════════════════════════════
    # BUILD CONTRACT PARAMETERS
    # ══════════════════════════════════════════════════
    def _build_params(self, signal, stake_info: dict) -> dict:
        """
        Build Deriv API contract parameters based on signal type.
        """
        deriv_type = self.CONTRACT_MAP.get(signal.contract_type)
        if not deriv_type:
            return None

        base_params = {
            "amount": stake_info["stake"],
            "basis": "stake",
            "currency": self.api.currency,
            "symbol": signal.symbol,
        }

        if signal.contract_type in ("ONLY_UPS", "ONLY_DOWNS"):
            # Run High/Low contracts — tick-based duration
            base_params["contract_type"] = deriv_type
            base_params["duration"] = signal.tick_count
            base_params["duration_unit"] = "t"  # ticks

        elif signal.contract_type in ("HIGH", "LOW"):
            # Rise/Fall or Higher/Lower with barrier
            base_params["contract_type"] = deriv_type
            base_params["duration"] = 5
            base_params["duration_unit"] = "t"

            if signal.barrier:
                base_params["barrier"] = signal.barrier

        return base_params

    # ══════════════════════════════════════════════════
    # MONITOR CONTRACT RESULT
    # ══════════════════════════════════════════════════
    async def _monitor_contract(self, trade: TradeRecord):
        """
        Poll for contract result. For tick contracts this is fast.
        For longer contracts we poll periodically.
        """
        if not trade.contract_id:
            return

        max_wait = 60  # Maximum seconds to wait for result
        poll_interval = 0.5
        start = time.time()

        while time.time() - start < max_wait:
            try:
                resp = await self.api._send({
                    "proposal_open_contract": 1,
                    "contract_id": trade.contract_id,
                })

                if "error" in resp:
                    await asyncio.sleep(poll_interval)
                    continue

                poc = resp.get("proposal_open_contract", {})

                if not poc:
                    await asyncio.sleep(poll_interval)
                    continue

                is_sold = poc.get("is_sold", 0)
                is_expired = poc.get("is_expired", 0)
                status = poc.get("status", "")

                if is_sold or is_expired or status in ("sold", "lost", "won"):
                    # Contract finished
                    sell_price = float(poc.get("sell_price", 0))
                    buy_price = float(poc.get("buy_price", trade.stake))
                    trade.profit = sell_price - buy_price
                    trade.exit_price = float(poc.get("exit_tick", 0))
                    trade.close_time = time.time()

                    if trade.profit > 0:
                        trade.result = "WIN"
                        self.risk.register_win(trade.profit, trade.to_dict())
                        print(f"  ✅ {trade.id} WIN +${trade.profit:.2f} | "
                              f"Bal: ${self.risk.current_balance:.2f}")
                    else:
                        trade.result = "LOSS"
                        self.risk.register_loss(
                            abs(trade.profit), trade.to_dict()
                        )
                        print(f"  ❌ {trade.id} LOSS -${abs(trade.profit):.2f} | "
                              f"Bal: ${self.risk.current_balance:.2f}")

                    # Remove from active
                    self.active_trades.pop(trade.contract_id, None)

                    # Add to history
                    self.trade_history.append(trade.to_dict())
                    if len(self.trade_history) > 500:
                        self.trade_history = self.trade_history[-500:]

                    # Notify callbacks
                    if self.on_trade_closed:
                        asyncio.create_task(self.on_trade_closed(trade))

                    # Check for mode changes to notify
                    await self._check_mode_changes(trade)

                    return

            except Exception as e:
                print(f"  ⚠️ Monitor error: {e}")

            await asyncio.sleep(poll_interval)

        # Timeout — assume lost
        print(f"  ⚠️ {trade.id} monitoring timed out")
        trade.result = "TIMEOUT"
        trade.close_time = time.time()
        self.active_trades.pop(trade.contract_id, None)

    async def _check_mode_changes(self, trade: TradeRecord):
        """Notify about recovery or compound activation"""
        if trade.result == "LOSS" and self.risk.recovery_active:
            if self.on_recovery_start:
                tier_cfg = self.risk.get_status()
                recovery = tier_cfg.get("recovery", {})
                if recovery:
                    asyncio.create_task(self.on_recovery_start(recovery))

            # Check for emergency
            if self.risk.locked and self.on_emergency:
                asyncio.create_task(
                    self.on_emergency(
                        self.risk.lock_reason,
                        self.risk.current_balance
                    )
                )

        if trade.result == "WIN" and self.risk.compound_active:
            if self.on_compound_start:
                asyncio.create_task(self.on_compound_start({
                    "streak": self.risk.consecutive_wins,
                    "multiplier": self.risk.compound_multiplier,
                }))

    # ══════════════════════════════════════════════════
    # RECOVERY TRADE — Special execution path
    # ══════════════════════════════════════════════════
    async def execute_recovery(self, symbol: str, direction: str,
                                barrier: str, stake_info: dict) -> TradeRecord:
        """
        Execute a High/Low recovery trade with widened barrier.
        Used when Only Ups/Downs lost and we need to recover.
        """
        from engine.filters import TradeSignal

        signal = TradeSignal()
        signal.symbol = symbol
        signal.direction = direction
        signal.contract_type = "HIGH" if direction == "UP" else "LOW"
        signal.barrier = barrier
        signal.composite_score = 60  # Recovery doesn't need high confidence
        signal.reason = f"RECOVERY Step {self.risk.recovery_step + 1}"

        return await self.execute(signal, stake_info)

    # ══════════════════════════════════════════════════
    # STATUS AND HISTORY
    # ══════════════════════════════════════════════════
    def get_active_trades(self) -> list:
        return [t.to_dict() for t in self.active_trades.values()]

    def get_trade_history(self, limit: int = 50) -> list:
        return self.trade_history[-limit:]

    def get_stats(self) -> dict:
        if not self.trade_history:
            return {
                "total": 0, "wins": 0, "losses": 0,
                "win_rate": 0, "total_profit": 0,
                "avg_win": 0, "avg_loss": 0,
                "best_trade": 0, "worst_trade": 0,
                "by_type": {}, "by_symbol": {},
            }

        wins = [t for t in self.trade_history if t["result"] == "WIN"]
        losses = [t for t in self.trade_history if t["result"] == "LOSS"]

        total_profit = sum(t["profit"] for t in self.trade_history)
        avg_win = (sum(t["profit"] for t in wins) / len(wins)) if wins else 0
        avg_loss = (sum(t["profit"] for t in losses) / len(losses)) if losses else 0

        profits = [t["profit"] for t in self.trade_history]

        # Stats by contract type
        by_type = {}
        for t in self.trade_history:
            ct = t["contract_type"]
            if ct not in by_type:
                by_type[ct] = {"trades": 0, "wins": 0, "profit": 0}
            by_type[ct]["trades"] += 1
            if t["result"] == "WIN":
                by_type[ct]["wins"] += 1
            by_type[ct]["profit"] += t["profit"]

        for ct in by_type:
            total = by_type[ct]["trades"]
            by_type[ct]["win_rate"] = round(
                by_type[ct]["wins"] / total * 100, 1
            ) if total > 0 else 0

        # Stats by symbol
        by_symbol = {}
        for t in self.trade_history:
            sym = t["symbol"]
            if sym not in by_symbol:
                by_symbol[sym] = {"trades": 0, "wins": 0, "profit": 0}
            by_symbol[sym]["trades"] += 1
            if t["result"] == "WIN":
                by_symbol[sym]["wins"] += 1
            by_symbol[sym]["profit"] += t["profit"]

        for sym in by_symbol:
            total = by_symbol[sym]["trades"]
            by_symbol[sym]["win_rate"] = round(
                by_symbol[sym]["wins"] / total * 100, 1
            ) if total > 0 else 0
            by_symbol[sym]["profit"] = round(by_symbol[sym]["profit"], 2)

        # Find best performing index
        best_index = max(by_symbol.keys(),
                        key=lambda k: by_symbol[k]["profit"]) if by_symbol else ""

        return {
            "total": len(self.trade_history),
            "wins": len(wins),
            "losses": len(losses),
            "win_rate": round(len(wins)/len(self.trade_history)*100, 1),
            "total_profit": round(total_profit, 2),
            "avg_win": round(avg_win, 2),
            "avg_loss": round(avg_loss, 2),
            "best_trade": round(max(profits), 2) if profits else 0,
            "worst_trade": round(min(profits), 2) if profits else 0,
            "best_index": best_index,
            "by_type": by_type,
            "by_symbol": by_symbol,
        }

    def can_trade(self) -> bool:
        """Check if we can accept new trades"""
        return (
            len(self.active_trades) < self.max_concurrent and
            not self._executing and
            not self.risk.locked and
            time.time() >= self.risk.cooldown_until
        )