"""
═══════════════════════════════════════════════════════════════
  DERIV SMART TRADER — MASTER ORCHESTRATOR
  Wires every component together. Single entry point.
═══════════════════════════════════════════════════════════════

  Usage:
    python main.py

  Environment variables (or .env file):
    DERIV_APP_ID=your_app_id
    DERIV_API_TOKEN=your_api_token
    TELEGRAM_BOT_TOKEN=your_bot_token  (optional)
    TELEGRAM_CHAT_ID=your_chat_id      (optional)
"""
import asyncio
import time
import datetime
import sys

from config import (
    VOLATILITY_INDICES, DASHBOARD_HOST, DASHBOARD_PORT, RISK
)
from engine.connection import DerivAPI
from engine.indicators import Indicators
from engine.analyzer import MarketAnalyzer
from engine.filters import SmartFilterEngine
from engine.risk import RiskEngine
from engine.executor import TradeExecutor
from engine.scanner import VolatilityScanner
from telegram_bot import TelegramNotifier


class SmartTrader:
    """
    The conductor. Initializes, connects, and runs
    every component in harmony.
    """

    def __init__(self):
        # Core components
        self.api = DerivAPI()
        self.risk = RiskEngine()
        self.analyzer = MarketAnalyzer(self.api)
        self.filters = SmartFilterEngine()
        self.executor = TradeExecutor(self.api, self.risk, self.analyzer)
        self.scanner = VolatilityScanner(self.analyzer, self.filters)
        self.telegram = TelegramNotifier()

        # Dashboard reference (set by dashboard.py)
        self.dashboard = None

        # State
        self.running = False
        self.start_time = 0
        self.ticks_processed = 0
        self.last_status_push = 0
        self.last_scanner_notify = 0

        # Wire up executor callbacks
        self.executor.on_trade_opened = self._on_trade_opened
        self.executor.on_trade_closed = self._on_trade_closed
        self.executor.on_recovery_start = self._on_recovery_start
        self.executor.on_compound_start = self._on_compound_start
        self.executor.on_emergency = self._on_emergency

    # ══════════════════════════════════════════════════
    # STARTUP SEQUENCE
    # ══════════════════════════════════════════════════
    async def start(self):
        """Full startup sequence"""
        self.start_time = time.time()

        print("═" * 55)
        print("  🤖 DERIV SMART TRADER")
        print("  ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
        print("  Starting all systems...")
        print("═" * 55)

        # 1. Connect to Deriv
        print("\n[1/6] Connecting to Deriv API...")
        connected = await self.api.connect()
        if not connected:
            print("❌ Failed to connect. Check credentials.")
            return False

        # 2. Initialize risk engine
        print("\n[2/6] Initializing risk engine...")
        self.risk.initialize(self.api.balance)
        self.filters.set_account_tier(self.risk.tier)

        # 3. Cold start — backfill all market data
        print("\n[3/6] Loading market data (cold start)...")
        await self.analyzer.cold_start()

        # 4. Start Telegram
        print("\n[4/6] Starting Telegram notifier...")
        asyncio.create_task(self.telegram.start())

        # 5. Subscribe to live ticks
        print("\n[5/6] Subscribing to live tick streams...")
        for symbol in VOLATILITY_INDICES:
            await self.api.subscribe_ticks(symbol, self._on_tick)
            await asyncio.sleep(0.1)  # Small delay between subscriptions

        # 6. Start scanner loop
        print("\n[6/6] Starting volatility scanner...")
        asyncio.create_task(
            self.scanner.run_loop(trade_callback=self._on_signal)
        )

        # Start periodic tasks
        asyncio.create_task(self._status_loop())
        asyncio.create_task(self._daily_reset_loop())

        # Optional: Telegram command listener
        asyncio.create_task(
            self.telegram.poll_commands(self._handle_telegram_command)
        )

        self.running = True

        print("\n" + "═" * 55)
        print("  ✅ ALL SYSTEMS ACTIVE")
        print(f"  💰 Balance: ${self.api.balance:.2f}")
        print(f"  📊 Tier: {self.risk.tier}")
        print(f"  🌐 Dashboard: http://localhost:{DASHBOARD_PORT}")
        print("═" * 55 + "\n")

        return True

    # ══════════════════════════════════════════════════
    # TICK HANDLER — Called for every tick on every index
    # ══════════════════════════════════════════════════
    async def _on_tick(self, tick_data: dict):
        """
        Heart of the bot. Every tick flows through here.
        """
        symbol = tick_data.get("symbol", "")
        if not symbol:
            return

        self.ticks_processed += 1

        # Update analyzer (processes tick, updates indicators)
        candle_closed = self.analyzer.process_tick(symbol, tick_data)

        # Quick scan this specific index
        score = self.scanner.scan_index(symbol)

        # Push tick to dashboard
        if self.dashboard:
            market = self.analyzer.get_market(symbol)
            if market:
                await self.dashboard.push_tick(
                    symbol,
                    float(tick_data.get("quote", 0)),
                    market.tick_analysis
                )

                # Push updated scanner data periodically
                if self.ticks_processed % 10 == 0:
                    await self.dashboard.push_scanner(
                        self.scanner.get_scanner_data()
                    )

    # ══════════════════════════════════════════════════
    # SIGNAL HANDLER — Called when scanner finds opportunity
    # ══════════════════════════════════════════════════
    async def _on_signal(self, signal):
        """
        Called by scanner when a tradeable signal is found.
        Validates, calculates stake, and executes.
        """
        if not self.running:
            return

        if not self.executor.can_trade():
            return

        # Double-check the signal is still valid
        market = self.analyzer.get_market(signal.symbol)
        if not market or not self.analyzer.is_ready(signal.symbol):
            return

        # Determine if this is a recovery trade
        is_recovery = self.risk.recovery_active
        contract_type = signal.contract_type

        # For recovery, prefer High/Low with wide barrier
        if is_recovery and contract_type in ("ONLY_UPS", "ONLY_DOWNS"):
            contract_type = "HIGH" if signal.direction == "UP" else "LOW"
            signal.contract_type = contract_type
            signal.barrier = self.filters.get_recovery_barrier(market)
            signal.tick_count = 0

        # Get payout estimate for stake calculation
        payout_ratio = None
        if is_recovery:
            # For recovery, estimate conservative payout
            payout_ratio = 0.50  # Wide barrier ≈ 50% payout

        # Calculate stake
        stake_info = self.risk.calculate_stake(
            confidence=signal.composite_score,
            contract_type=contract_type,
            payout_ratio=payout_ratio
        )

        if not stake_info["trade"]:
            if stake_info["reason"] in ("DAILY LOSS LIMIT",
                                         "SESSION DRAWDOWN LIMIT",
                                         "BALANCE TOO LOW"):
                if self.telegram:
                    await self.telegram.notify_emergency(
                        stake_info["reason"], self.risk.current_balance
                    )
            return

        # Mark symbol as trading in scanner
        self.scanner.mark_trading(signal.symbol)

        # Execute
        trade = await self.executor.execute(signal, stake_info)

        if trade is None:
            self.scanner.mark_available(signal.symbol)

    # ══════════════════════════════════════════════════
    # TRADE CALLBACKS
    # ══════════════════════════════════════════════════
    async def _on_trade_opened(self, trade):
        """Fires when a trade is successfully placed"""
        # Telegram notification
        await self.telegram.notify_trade_opened(trade)

        # Dashboard update
        if self.dashboard:
            await self.dashboard.push_trade(trade.to_dict())
            await self.dashboard.push_account(self.risk.get_status())

    async def _on_trade_closed(self, trade):
        """Fires when a trade result is determined"""
        # Free up the symbol for scanner
        self.scanner.mark_available(trade.symbol)

        # Telegram notification
        await self.telegram.notify_trade_closed(trade)

        # Update filter tier if balance changed tiers
        self.filters.set_account_tier(self.risk.tier)

        # Dashboard updates
        if self.dashboard:
            await self.dashboard.push_trade(trade.to_dict())
            await self.dashboard.push_account(self.risk.get_status())
            await self.dashboard.push_performance(
                self.risk.get_equity_curve()
            )

        # Cooldown notification
        if self.risk.cooldown_until > time.time():
            remaining = int(self.risk.cooldown_until - time.time())
            await self.telegram.notify_cooldown(remaining)

    async def _on_recovery_start(self, recovery):
        """Recovery mode activated"""
        await self.telegram.notify_recovery_started(recovery)
        if self.dashboard:
            await self.dashboard.push_recovery(recovery)

    async def _on_compound_start(self, info):
        """Compound mode activated"""
        await self.telegram.notify_compound_activated(info)

    async def _on_emergency(self, reason, balance):
        """Emergency stop triggered"""
        await self.telegram.notify_emergency(reason, balance)
        if self.dashboard:
            await self.dashboard.push_account(self.risk.get_status())

    # ══════════════════════════════════════════════════
    # PERIODIC TASKS
    # ══════════════════════════════════════════════════
    async def _status_loop(self):
        """Push status updates periodically"""
        while self.running:
            try:
                await asyncio.sleep(30)

                # Update balance from API
                await self.api.get_balance()
                self.risk.update_balance(self.api.balance)

                # Dashboard full update
                if self.dashboard:
                    await self.dashboard.push_account(
                        self.risk.get_status()
                    )
                    await self.dashboard.push_scanner(
                        self.scanner.get_scanner_data()
                    )

                # Telegram scanner update every 15 minutes
                now = time.time()
                if now - self.last_scanner_notify > 900:
                    self.last_scanner_notify = now
                    await self.telegram.notify_hot_indices(
                        self.scanner.get_scanner_data()
                    )

            except Exception as e:
                print(f"⚠️ Status loop error: {e}")

    async def _daily_reset_loop(self):
        """Reset daily stats at midnight"""
        while self.running:
            try:
                # Calculate seconds until next midnight UTC
                utc_now = datetime.datetime.utcnow()
                midnight = utc_now.replace(
                    hour=0, minute=0, second=0, microsecond=0
                ) + datetime.timedelta(days=1)
                seconds_to_midnight = (midnight - utc_now).total_seconds()

                await asyncio.sleep(seconds_to_midnight)

                # Send daily summary
                daily = self.risk.get_daily_summary()
                exec_stats = self.executor.get_stats()
                await self.telegram.send_daily_summary(daily, exec_stats)

                # Reset
                self.risk.reset_daily()
                print("🌅 Daily reset complete")

            except Exception as e:
                print(f"⚠️ Daily reset error: {e}")
                await asyncio.sleep(3600)

    # ══════════════════════════════════════════════════
    # TELEGRAM COMMANDS
    # ══════════════════════════════════════════════════
    async def _handle_telegram_command(self, text: str):
        cmd = text.strip().lower()

        if cmd == "/status":
            status = self.risk.get_status()
            uptime = int(time.time() - self.start_time)
            hours, rem = divmod(uptime, 3600)
            mins, secs = divmod(rem, 60)
            msg = (
                f"📊 <b>BOT STATUS</b>\n"
                f"━━━━━━━━━━━━━━━━━━━━━\n"
                f"💰 ${status['balance']:.2f} "
                f"({status['session_pnl']:+.2f})\n"
                f"📈 Win Rate: {status['win_rate']}%\n"
                f"🎯 Trades: {status['total_trades']}\n"
                f"⚙️ {status['mode']}\n"
                f"⏱️ Uptime: {hours}h {mins}m {secs}s\n"
                f"📡 Ticks: {self.ticks_processed:,}\n"
                f"━━━━━━━━━━━━━━━━━━━━━"
            )
            await self.telegram.send(msg)

        elif cmd == "/stop":
            await self.telegram.send("⏹️ Stopping bot...")
            await self.stop()

        elif cmd == "/unlock":
            self.risk.unlock()
            await self.telegram.send("🔓 Account unlocked. Trading resumed.")

        elif cmd == "/summary":
            daily = self.risk.get_daily_summary()
            exec_stats = self.executor.get_stats()
            await self.telegram.send_daily_summary(daily, exec_stats)

        elif cmd == "/scan":
            await self.telegram.notify_hot_indices(
                self.scanner.get_scanner_data()
            )

    # ══════════════════════════════════════════════════
    # SHUTDOWN
    # ══════════════════════════════════════════════════
    async def stop(self):
        print("\n⏹️ Shutting down...")
        self.running = False
        self.scanner.stop()

        await self.telegram.send(
            "🔴 <b>Bot shutting down</b>\n"
            f"💰 Final balance: ${self.risk.current_balance:.2f}"
        )

        await self.telegram.stop()
        await self.api.disconnect()
        print("✅ Shutdown complete")

    # ══════════════════════════════════════════════════
    # API FOR DASHBOARD CONTROLS
    # ══════════════════════════════════════════════════
    def get_full_state(self) -> dict:
        """Complete state snapshot for dashboard"""
        return {
            "account": self.risk.get_status(),
            "scanner": self.scanner.get_scanner_data(),
            "active_trades": self.executor.get_active_trades(),
            "trade_history": self.executor.get_trade_history(50),
            "trade_stats": self.executor.get_stats(),
            "equity_curve": self.risk.get_equity_curve(),
            "uptime": int(time.time() - self.start_time),
            "ticks_processed": self.ticks_processed,
            "running": self.running,
        }

    def update_settings(self, settings: dict):
        """Update settings from dashboard"""
        if "max_ticks" in settings:
            tier = self.risk.tier
            from config import ACCOUNT_TIERS
            max_allowed = ACCOUNT_TIERS.get(tier, {}).get("max_ticks", 5)
            val = min(int(settings["max_ticks"]), max_allowed)
            self.filters.max_ticks_allowed = val

        if "daily_loss_limit" in settings:
            RISK["daily_loss_limit_pct"] = float(settings["daily_loss_limit"])

        if "compound_trigger" in settings:
            RISK["compound_trigger_wins"] = int(settings["compound_trigger"])

        if "scan_interval" in settings:
            self.scanner.scan_interval = float(settings["scan_interval"])

        if "max_concurrent" in settings:
            self.executor.max_concurrent = int(settings["max_concurrent"])


# Global instance
trader = SmartTrader()


async def main():
    success = await trader.start()
    if not success:
        sys.exit(1)

    # Keep running until interrupted
    try:
        await asyncio.Event().wait()
    except (KeyboardInterrupt, SystemExit):
        await trader.stop()


if __name__ == "__main__":
    # Run with dashboard — import starts the server
    from dashboard import start_all
    start_all()