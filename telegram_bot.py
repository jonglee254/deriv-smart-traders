"""
═══════════════════════════════════════════════════════════════
  TELEGRAM NOTIFICATION SYSTEM
  Real-time alerts for trades, recoveries, and emergencies.
  Clean formatting with status emojis.
═══════════════════════════════════════════════════════════════
"""
import asyncio
import time
import aiohttp
from config import TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID, VOLATILITY_INDICES


class TelegramNotifier:

    def __init__(self, bot_token: str = None, chat_id: str = None):
        self.bot_token = bot_token or TELEGRAM_BOT_TOKEN
        self.chat_id = chat_id or TELEGRAM_CHAT_ID
        self.base_url = f"https://api.telegram.org/bot{self.bot_token}"
        self.enabled = bool(self.bot_token and self.chat_id)
        self._queue = asyncio.Queue()
        self._last_sent = 0
        self._min_interval = 0.4  # Telegram rate limit safety
        self._running = False
        self._session = None

        if not self.enabled:
            print("📱 Telegram: DISABLED (no token/chat_id)")
        else:
            print("📱 Telegram: ENABLED")

    # ══════════════════════════════════════════════════
    # QUEUE PROCESSOR
    # ══════════════════════════════════════════════════
    async def start(self):
        """Background task — processes message queue"""
        if not self.enabled:
            return

        self._running = True
        self._session = aiohttp.ClientSession()

        # Send startup message
        await self._queue.put(
            "🤖 <b>DERIV SMART TRADER</b>\n"
            "━━━━━━━━━━━━━━━━━━━━━\n"
            "✅ Bot is now <b>ONLINE</b>\n"
            "📊 Monitoring all Volatility Indices\n"
            "━━━━━━━━━━━━━━━━━━━━━"
        )

        while self._running:
            try:
                msg = await asyncio.wait_for(
                    self._queue.get(), timeout=60
                )

                now = time.time()
                wait = self._min_interval - (now - self._last_sent)
                if wait > 0:
                    await asyncio.sleep(wait)

                await self._send_message(msg)
                self._last_sent = time.time()

            except asyncio.TimeoutError:
                continue
            except Exception as e:
                print(f"📱 Telegram queue error: {e}")
                await asyncio.sleep(1)

    async def stop(self):
        self._running = False
        if self._session:
            await self._session.close()

    async def _send_message(self, text: str):
        if not self.enabled or not self._session:
            return

        try:
            async with self._session.post(
                f"{self.base_url}/sendMessage",
                json={
                    "chat_id": self.chat_id,
                    "text": text,
                    "parse_mode": "HTML",
                    "disable_web_page_preview": True,
                },
                timeout=aiohttp.ClientTimeout(total=10)
            ) as resp:
                if resp.status != 200:
                    data = await resp.json()
                    print(f"📱 Telegram error: {data}")
        except Exception as e:
            print(f"📱 Telegram send failed: {e}")

    def _enqueue(self, msg: str):
        if self.enabled:
            try:
                self._queue.put_nowait(msg)
            except asyncio.QueueFull:
                pass  # Drop message if queue is full

    # ══════════════════════════════════════════════════
    # TRADE NOTIFICATIONS
    # ══════════════════════════════════════════════════
    async def notify_trade_opened(self, trade):
        """Called when a trade is executed"""
        t = trade.to_dict() if hasattr(trade, 'to_dict') else trade
        symbol_name = VOLATILITY_INDICES.get(
            t["symbol"], {}
        ).get("name", t["symbol"])

        direction_icon = "🟢 ▲" if t["direction"] == "UP" else "🔴 ▼"
        tick_info = f"🎯 Ticks: <b>{t.get('tick_count', '-')}</b>\n" \
            if t.get("tick_count") else ""
        barrier_info = f"📏 Barrier: {t.get('barrier', '-')}\n" \
            if t.get("barrier") else ""

        msg = (
            f"{direction_icon} <b>TRADE OPENED</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━━\n"
            f"📊 {symbol_name}\n"
            f"📋 <b>{t['contract_type']}</b>\n"
            f"{tick_info}"
            f"{barrier_info}"
            f"💰 Stake: <b>${t['stake']:.2f}</b>\n"
            f"💵 Payout: <b>${t.get('potential_payout', 0):.2f}</b>\n"
            f"🎰 Confidence: <b>{t.get('confidence', 0):.0f}%</b>\n"
            f"⚙️ Mode: {t.get('mode', 'NORMAL')}\n"
            f"━━━━━━━━━━━━━━━━━━━━━"
        )
        self._enqueue(msg)

    async def notify_trade_closed(self, trade):
        """Called when a trade result is determined"""
        t = trade.to_dict() if hasattr(trade, 'to_dict') else trade
        symbol_name = VOLATILITY_INDICES.get(
            t["symbol"], {}
        ).get("name", t["symbol"])

        is_win = t.get("result") == "WIN"
        profit = t.get("profit", 0)

        result_icon = "✅ WIN" if is_win else "❌ LOSS"
        money_icon = "💵" if is_win else "💸"

        msg = (
            f"{result_icon} | {symbol_name}\n"
            f"━━━━━━━━━━━━━━━━━━━━━\n"
            f"📋 {t['contract_type']}"
            f"{' ' + str(t.get('tick_count', '')) + 'T' if t.get('tick_count') else ''}\n"
            f"{money_icon} {'Profit' if is_win else 'Loss'}: "
            f"<b>${abs(profit):.2f}</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━━"
        )
        self._enqueue(msg)

    # ══════════════════════════════════════════════════
    # ACCOUNT STATUS NOTIFICATIONS
    # ══════════════════════════════════════════════════
    async def notify_balance_update(self, status: dict):
        """Periodic balance update (not every trade)"""
        mode = status.get("mode", "NORMAL")
        pnl = status.get("session_pnl", 0)
        pnl_icon = "📈" if pnl >= 0 else "📉"

        msg = (
            f"💼 <b>ACCOUNT STATUS</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━━\n"
            f"💰 Balance: <b>${status['balance']:.2f}</b>\n"
            f"{pnl_icon} Session: <b>${pnl:+.2f} "
            f"({status.get('session_pnl_pct', 0):+.1f}%)</b>\n"
            f"📊 Win Rate: {status.get('win_rate', 0):.1f}% "
            f"({status.get('total_wins', 0)}/{status.get('total_trades', 0)})\n"
            f"🏷️ Tier: {status.get('tier', '-')}\n"
            f"⚙️ {mode}\n"
            f"━━━━━━━━━━━━━━━━━━━━━"
        )
        self._enqueue(msg)

    # ══════════════════════════════════════════════════
    # RECOVERY NOTIFICATIONS
    # ══════════════════════════════════════════════════
    async def notify_recovery_started(self, recovery: dict):
        """Recovery mode activated"""
        msg = (
            f"🔄 <b>RECOVERY MODE</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━━\n"
            f"📉 Loss to recover: "
            f"<b>${recovery.get('accumulated_loss', 0):.2f}</b>\n"
            f"🔢 Step: {recovery.get('step', 1)}/"
            f"{recovery.get('max_steps', 3)}\n"
            f"📏 Barrier: WIDENED for higher win rate\n"
            f"━━━━━━━━━━━━━━━━━━━━━"
        )
        self._enqueue(msg)

    async def notify_recovery_success(self, recovered: float):
        """Recovery completed successfully"""
        msg = (
            f"✅ <b>RECOVERY SUCCESS</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━━\n"
            f"💵 Recovered: <b>${recovered:.2f}</b>\n"
            f"🔄 Back to normal trading\n"
            f"━━━━━━━━━━━━━━━━━━━━━"
        )
        self._enqueue(msg)

    async def notify_recovery_failed(self, total_loss: float):
        """Recovery exhausted all steps"""
        msg = (
            f"⚠️ <b>RECOVERY EXHAUSTED</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━━\n"
            f"💸 Accepted loss: <b>${total_loss:.2f}</b>\n"
            f"🔄 Resetting to normal mode\n"
            f"🛡️ Account protected — no chase\n"
            f"━━━━━━━━━━━━━━━━━━━━━"
        )
        self._enqueue(msg)

    # ══════════════════════════════════════════════════
    # COMPOUND NOTIFICATIONS
    # ══════════════════════════════════════════════════
    async def notify_compound_activated(self, info: dict):
        """Compounding mode activated after win streak"""
        msg = (
            f"🚀 <b>COMPOUNDING ACTIVE</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━━\n"
            f"🔥 Win Streak: <b>{info.get('streak', 0)}</b>\n"
            f"📈 Multiplier: <b>{info.get('multiplier', 1):.1f}x</b>\n"
            f"💡 Riding the momentum!\n"
            f"━━━━━━━━━━━━━━━━━━━━━"
        )
        self._enqueue(msg)

    async def notify_compound_ended(self, streak: int, total_profit: float):
        """Compounding ended (loss or manual)"""
        msg = (
            f"⏹️ <b>COMPOUND ENDED</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━━\n"
            f"🔥 Final Streak: {streak}\n"
            f"💵 Compound Profit: ${total_profit:.2f}\n"
            f"🔄 Back to normal stakes\n"
            f"━━━━━━━━━━━━━━━━━━━━━"
        )
        self._enqueue(msg)

    # ══════════════════════════════════════════════════
    # EMERGENCY NOTIFICATIONS
    # ══════════════════════════════════════════════════
    async def notify_emergency(self, reason: str, balance: float):
        """Bot stopped for safety"""
        msg = (
            f"🚨🚨🚨 <b>BOT STOPPED</b> 🚨🚨🚨\n"
            f"━━━━━━━━━━━━━━━━━━━━━\n"
            f"⛔ Reason: <b>{reason}</b>\n"
            f"💼 Balance: <b>${balance:.2f}</b>\n"
            f"🛡️ Account protected\n"
            f"━━━━━━━━━━━━━━━━━━━━━\n"
            f"⚠️ Restart manually or unlock\n"
            f"from the dashboard when ready."
        )
        self._enqueue(msg)

    async def notify_cooldown(self, seconds: int):
        """Cooldown activated after consecutive losses"""
        msg = (
            f"⏸️ <b>COOLDOWN ACTIVE</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━━\n"
            f"⏱️ Pausing for <b>{seconds}s</b>\n"
            f"🧊 Letting market settle\n"
            f"━━━━━━━━━━━━━━━━━━━━━"
        )
        self._enqueue(msg)

    # ══════════════════════════════════════════════════
    # DAILY SUMMARY
    # ══════════════════════════════════════════════════
    async def send_daily_summary(self, stats: dict, exec_stats: dict = None):
        """End of day performance report"""
        pnl = stats.get("pnl", 0)
        pnl_icon = "📈" if pnl >= 0 else "📉"
        result_icon = "🏆" if pnl >= 0 else "📉"

        msg = (
            f"📊 <b>DAILY SUMMARY</b>\n"
            f"{'═' * 23}\n"
            f"💼 Start: ${stats.get('starting', 0):.2f}\n"
            f"💰 End:   ${stats.get('ending', 0):.2f}\n"
            f"{pnl_icon} P&L: <b>${pnl:+.2f} "
            f"({stats.get('pnl_pct', 0):+.1f}%)</b>\n"
            f"{'═' * 23}\n"
            f"🎯 Trades: {stats.get('total_trades', 0)}\n"
            f"✅ Wins: {stats.get('wins', 0)} | "
            f"❌ Losses: {stats.get('losses', 0)}\n"
            f"📊 Win Rate: <b>{stats.get('win_rate', 0):.1f}%</b>\n"
            f"🔥 Best Streak: {stats.get('best_streak', 0)}\n"
            f"📉 Max Drawdown: ${stats.get('max_drawdown', 0):.2f}\n"
            f"🔄 Recovery Events: {stats.get('recovery_events', 0)}\n"
        )

        if exec_stats:
            best_idx = exec_stats.get("best_index", "N/A")
            best_name = VOLATILITY_INDICES.get(
                best_idx, {}
            ).get("name", best_idx)

            msg += (
                f"{'═' * 23}\n"
                f"🏆 Best Index: {best_name}\n"
                f"💵 Avg Win: ${exec_stats.get('avg_win', 0):.2f}\n"
                f"💸 Avg Loss: ${exec_stats.get('avg_loss', 0):.2f}\n"
            )

            # Per contract type breakdown
            by_type = exec_stats.get("by_type", {})
            if by_type:
                msg += f"{'═' * 23}\n"
                for ct, data in by_type.items():
                    msg += (f"📋 {ct}: {data.get('win_rate', 0):.0f}% "
                           f"(${data.get('profit', 0):+.2f})\n")

        msg += f"{'═' * 23}\n{result_icon} End of day report."

        self._enqueue(msg)

    # ══════════════════════════════════════════════════
    # SCANNER NOTIFICATIONS (Periodic)
    # ══════════════════════════════════════════════════
    async def notify_hot_indices(self, scanner_data: list):
        """
        Periodic notification about which indices are hot.
        Only sent every 10-15 minutes to avoid spam.
        """
        ready = [s for s in scanner_data if s.get("status") == "READY"]
        trading = [s for s in scanner_data if s.get("status") == "TRADING"]

        if not ready and not trading:
            return

        msg = "🔍 <b>MARKET SCAN</b>\n━━━━━━━━━━━━━━━━━━━━━\n"

        if trading:
            for s in trading:
                dir_icon = "▲" if s["direction"] == "UP" else "▼"
                msg += (f"🔥 {s['name']}: {dir_icon} "
                       f"Score {s['score']:.0f}\n")

        if ready:
            for s in ready[:3]:  # Top 3 only
                dir_icon = "▲" if s["direction"] == "UP" else "▼"
                msg += (f"⚡ {s['name']}: {dir_icon} "
                       f"Score {s['score']:.0f} | "
                       f"{s['max_ticks']}T\n")

        no_trend = sum(1 for s in scanner_data if s.get("status") == "NO_TREND")
        msg += f"━━━━━━━━━━━━━━━━━━━━━\n💤 {no_trend} indices flat"

        self._enqueue(msg)

    # ══════════════════════════════════════════════════
    # MANUAL MESSAGE
    # ══════════════════════════════════════════════════
    async def send(self, text: str):
        """Send a custom message"""
        self._enqueue(text)

    # ══════════════════════════════════════════════════
    # COMMAND HANDLER (optional bot commands)
    # ══════════════════════════════════════════════════
    async def poll_commands(self, command_handler=None):
        """
        Optional: listen for Telegram commands like
        /status, /stop, /unlock, /summary
        """
        if not self.enabled or not self._session:
            return

        offset = 0

        while self._running:
            try:
                async with self._session.get(
                    f"{self.base_url}/getUpdates",
                    params={"offset": offset, "timeout": 30},
                    timeout=aiohttp.ClientTimeout(total=35)
                ) as resp:
                    if resp.status != 200:
                        await asyncio.sleep(5)
                        continue

                    data = await resp.json()
                    results = data.get("result", [])

                    for update in results:
                        offset = update["update_id"] + 1
                        message = update.get("message", {})
                        text = message.get("text", "")
                        chat_id = str(message.get("chat", {}).get("id", ""))

                        # Only process from our authorized chat
                        if chat_id != self.chat_id:
                            continue

                        if command_handler:
                            await command_handler(text)
                        else:
                            await self._handle_default_command(text)

            except asyncio.TimeoutError:
                continue
            except Exception as e:
                print(f"📱 Command poll error: {e}")
                await asyncio.sleep(5)

    async def _handle_default_command(self, text: str):
        """Built-in command responses"""
        cmd = text.strip().lower()

        if cmd == "/status":
            self._enqueue("📊 Use the dashboard for full status.\n"
                         "🌐 Open your browser to the dashboard URL.")
        elif cmd == "/help":
            self._enqueue(
                "🤖 <b>Available Commands</b>\n"
                "━━━━━━━━━━━━━━━━━━━━━\n"
                "/status — Quick status check\n"
                "/help — Show this message\n"
                "━━━━━━━━━━━━━━━━━━━━━\n"
                "💡 Full control available on\n"
                "the web dashboard."
            )
        elif cmd == "/ping":
            self._enqueue("🏓 Pong! Bot is alive.")