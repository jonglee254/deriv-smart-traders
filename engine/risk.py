"""
═══════════════════════════════════════════════════════════════
  RISK ENGINE — Account Protection + Recovery + Compounding
  Adapts to account size. Cannot wipe an account.
═══════════════════════════════════════════════════════════════
"""
import time
from config import ACCOUNT_TIERS, RISK


class RiskEngine:

    def __init__(self):
        # Account state
        self.starting_balance = 0.0
        self.current_balance = 0.0
        self.session_high = 0.0
        self.session_low = float("inf")
        self.daily_pnl = 0.0
        self.tier = "MICRO"

        # Trade tracking
        self.total_trades = 0
        self.total_wins = 0
        self.total_losses = 0
        self.consecutive_wins = 0
        self.consecutive_losses = 0
        self.best_streak = 0
        self.worst_streak = 0
        self.max_drawdown = 0.0

        # Recovery state
        self.recovery_active = False
        self.recovery_step = 0
        self.recovery_accumulated = 0.0
        self.recovery_history = []

        # Compound state
        self.compound_active = False
        self.compound_multiplier = 1.0

        # Safety
        self.locked = False
        self.lock_reason = ""
        self.cooldown_until = 0
        self.trade_history = []

    # ══════════════════════════════════════════════════
    # INITIALIZATION
    # ══════════════════════════════════════════════════
    def initialize(self, balance: float):
        self.starting_balance = balance
        self.current_balance = balance
        self.session_high = balance
        self.session_low = balance
        self.tier = self._detect_tier(balance)

        tier_cfg = ACCOUNT_TIERS[self.tier]
        print(f"💰 Risk Engine initialized")
        print(f"   Balance:  ${balance:.2f}")
        print(f"   Tier:     {self.tier}")
        print(f"   Base Risk: {tier_cfg['base_risk_pct']}%")
        print(f"   Recovery:  {tier_cfg['max_recovery']} steps max")
        print(f"   Compound:  {tier_cfg['compound_max']}x max")
        print(f"   Max Ticks: {tier_cfg['max_ticks']}")

    def _detect_tier(self, balance: float) -> str:
        for name, cfg in ACCOUNT_TIERS.items():
            if cfg["min"] <= balance < cfg["max"]:
                return name
        return "MICRO"

    def update_balance(self, new_balance: float):
        self.current_balance = new_balance
        self.session_high = max(self.session_high, new_balance)
        self.session_low = min(self.session_low, new_balance)

        dd = self.session_high - new_balance
        self.max_drawdown = max(self.max_drawdown, dd)

        old_tier = self.tier
        self.tier = self._detect_tier(new_balance)
        if self.tier != old_tier:
            print(f"📊 Tier changed: {old_tier} → {self.tier}")

    # ══════════════════════════════════════════════════
    # STAKE CALCULATION — The core function
    # ══════════════════════════════════════════════════
    def calculate_stake(self, confidence: float,
                        contract_type: str,
                        payout_ratio: float = None) -> dict:
        """
        Returns stake amount and all metadata about the decision.
        """
        # ── Emergency checks ──
        if self.locked:
            return self._no_trade(f"LOCKED: {self.lock_reason}")

        if time.time() < self.cooldown_until:
            remaining = int(self.cooldown_until - time.time())
            return self._no_trade(f"COOLDOWN: {remaining}s remaining")

        min_stake = RISK["min_stake"]

        if self.current_balance < min_stake * RISK["min_trades_cushion"]:
            self._lock("BALANCE CRITICALLY LOW")
            return self._no_trade("BALANCE TOO LOW")

        # Daily loss limit
        daily_loss_pct = abs(self.daily_pnl) / self.starting_balance * 100 \
            if self.starting_balance > 0 and self.daily_pnl < 0 else 0

        if daily_loss_pct >= RISK["daily_loss_limit_pct"]:
            self._lock("DAILY LOSS LIMIT REACHED")
            return self._no_trade("DAILY LOSS LIMIT")

        # Session drawdown
        if self.session_high > 0:
            dd_pct = ((self.session_high - self.current_balance)
                      / self.session_high * 100)
            if dd_pct >= RISK["session_drawdown_pct"]:
                self._lock("SESSION DRAWDOWN LIMIT")
                return self._no_trade("SESSION DRAWDOWN LIMIT")

        # ── Base stake from tier ──
        tier_cfg = ACCOUNT_TIERS[self.tier]
        base_pct = tier_cfg["base_risk_pct"] / 100
        base_stake = self.current_balance * base_pct

        # ── Confidence adjustment (0.6x to 1.5x) ──
        conf_mult = 0.6 + (confidence / 100) * 0.9
        stake = base_stake * conf_mult

        # ── Mode: Recovery ──
        mode = "NORMAL"
        if self.recovery_active and contract_type in ("HIGH", "LOW"):
            rec = self._calc_recovery_stake(payout_ratio, tier_cfg)
            if rec["viable"]:
                stake = rec["stake"]
                mode = f"RECOVERY_STEP_{self.recovery_step + 1}"
            else:
                self._reset_recovery("Max steps or stake too high")
                mode = "NORMAL"

        # ── Mode: Compound ──
        if self.compound_active and not self.recovery_active:
            extra = (self.consecutive_wins - RISK["compound_trigger_wins"]) * 0.25
            self.compound_multiplier = min(
                1.0 + extra,
                RISK["compound_max_multiplier"],
                tier_cfg["compound_max"]
            )
            stake *= self.compound_multiplier
            mode = f"COMPOUND_{self.compound_multiplier:.1f}x"

        # ── Safety clamps ──
        max_single = self.current_balance * (RISK["max_single_trade_pct"] / 100)
        stake = max(min_stake, min(stake, max_single))

        # Ensure we keep enough for minimum trades cushion
        remaining = self.current_balance - stake
        min_remaining = min_stake * RISK["min_trades_cushion"]
        if remaining < min_remaining:
            stake = max(min_stake, self.current_balance - min_remaining)

        stake = round(stake, 2)

        if stake < min_stake:
            return self._no_trade("CALCULATED STAKE BELOW MINIMUM")

        risk_pct = (stake / self.current_balance * 100) if self.current_balance > 0 else 0

        return {
            "trade": True,
            "stake": stake,
            "mode": mode,
            "tier": self.tier,
            "risk_pct": round(risk_pct, 2),
            "balance": self.current_balance,
            "reason": mode,
            "recovery_step": self.recovery_step if self.recovery_active else None,
            "compound_mult": self.compound_multiplier if self.compound_active else None,
        }

    # ══════════════════════════════════════════════════
    # RECOVERY SYSTEM
    # ══════════════════════════════════════════════════
    def _calc_recovery_stake(self, payout_ratio: float,
                              tier_cfg: dict) -> dict:
        max_steps = tier_cfg["max_recovery"]

        if self.recovery_step >= max_steps:
            return {"viable": False, "stake": 0}

        target = self.recovery_accumulated + (RISK["min_stake"] * 2)

        if payout_ratio and payout_ratio > 0:
            needed = target / payout_ratio
        else:
            needed = target * 2.2

        # Progressive risk cap per step
        cap_pct = (5 + self.recovery_step * 2) / 100
        max_recovery = self.current_balance * cap_pct

        # Hard cap: never more than 15% on recovery
        absolute_cap = self.current_balance * 0.15

        if needed > min(max_recovery, absolute_cap):
            return {"viable": False, "stake": 0}

        return {"viable": True, "stake": round(needed, 2)}

    def _reset_recovery(self, reason: str = ""):
        if self.recovery_active:
            self.recovery_history.append({
                "steps": self.recovery_step,
                "accumulated": self.recovery_accumulated,
                "result": reason,
                "time": time.time()
            })
        self.recovery_active = False
        self.recovery_step = 0
        self.recovery_accumulated = 0.0

    # ══════════════════════════════════════════════════
    # TRADE RESULT REGISTRATION
    # ══════════════════════════════════════════════════
    def register_win(self, profit: float, trade_info: dict = None):
        self.current_balance += profit
        self.daily_pnl += profit
        self.session_high = max(self.session_high, self.current_balance)
        self.total_trades += 1
        self.total_wins += 1
        self.consecutive_wins += 1
        self.consecutive_losses = 0
        self.best_streak = max(self.best_streak, self.consecutive_wins)

        # Recovery: win means we recovered
        if self.recovery_active:
            self._reset_recovery("RECOVERED ✅")

        # Compound: activate after streak threshold
        if self.consecutive_wins >= RISK["compound_trigger_wins"]:
            self.compound_active = True

        # Update tier
        self.tier = self._detect_tier(self.current_balance)

        self._log_trade("WIN", profit, trade_info)

    def register_loss(self, loss: float, trade_info: dict = None):
        loss = abs(loss)
        self.current_balance -= loss
        self.daily_pnl -= loss
        self.session_low = min(self.session_low, self.current_balance)
        dd = self.session_high - self.current_balance
        self.max_drawdown = max(self.max_drawdown, dd)

        self.total_trades += 1
        self.total_losses += 1
        self.consecutive_losses += 1
        self.consecutive_wins = 0
        self.worst_streak = max(self.worst_streak, self.consecutive_losses)

        # Kill compounding immediately
        self.compound_active = False
        self.compound_multiplier = 1.0

        # Activate or advance recovery
        if not self.recovery_active:
            self.recovery_active = True
            self.recovery_step = 0
            self.recovery_accumulated = loss
        else:
            self.recovery_step += 1
            self.recovery_accumulated += loss

        # Check if recovery is still viable
        tier_cfg = ACCOUNT_TIERS.get(self.tier, ACCOUNT_TIERS["MICRO"])
        if self.recovery_step >= tier_cfg["max_recovery"]:
            self._reset_recovery("MAX STEPS REACHED — accepting loss")

        # Cooldown after consecutive losses
        if self.consecutive_losses >= RISK["cooldown_after_losses"]:
            self.cooldown_until = time.time() + RISK["cooldown_seconds"]

        # Update tier
        self.tier = self._detect_tier(self.current_balance)

        self._log_trade("LOSS", -loss, trade_info)

    # ══════════════════════════════════════════════════
    # COMPOUNDING LOGIC
    # ══════════════════════════════════════════════════
    def should_compound(self) -> bool:
        """External check for compound status"""
        return (self.compound_active and
                not self.recovery_active and
                self.consecutive_wins >= RISK["compound_trigger_wins"])

    def get_compound_info(self) -> dict:
        return {
            "active": self.compound_active,
            "multiplier": self.compound_multiplier,
            "streak": self.consecutive_wins,
            "trigger_at": RISK["compound_trigger_wins"],
        }

    # ══════════════════════════════════════════════════
    # SAFETY LOCKS
    # ══════════════════════════════════════════════════
    def _lock(self, reason: str):
        self.locked = True
        self.lock_reason = reason
        print(f"🔒 ACCOUNT LOCKED: {reason}")

    def unlock(self):
        """Manual unlock — called from dashboard or restart"""
        self.locked = False
        self.lock_reason = ""
        print("🔓 Account unlocked")

    def reset_daily(self):
        """Called at start of new trading day"""
        self.daily_pnl = 0.0
        self.starting_balance = self.current_balance
        self.session_high = self.current_balance
        self.session_low = self.current_balance
        self.max_drawdown = 0.0
        self.locked = False
        self.lock_reason = ""
        self.cooldown_until = 0
        self._reset_recovery("Daily reset")
        self.compound_active = False
        self.compound_multiplier = 1.0
        self.consecutive_wins = 0
        self.consecutive_losses = 0
        print("🌅 Daily reset complete")

    # ══════════════════════════════════════════════════
    # HELPERS
    # ══════════════════════════════════════════════════
    def _no_trade(self, reason: str) -> dict:
        return {
            "trade": False,
            "stake": 0,
            "mode": "BLOCKED",
            "tier": self.tier,
            "risk_pct": 0,
            "balance": self.current_balance,
            "reason": reason,
        }

    def _log_trade(self, result: str, amount: float, info: dict = None):
        entry = {
            "result": result,
            "amount": amount,
            "balance": self.current_balance,
            "tier": self.tier,
            "time": time.time(),
            "info": info or {}
        }
        self.trade_history.append(entry)
        # Keep last 1000 trades
        if len(self.trade_history) > 1000:
            self.trade_history = self.trade_history[-1000:]

    def get_win_rate(self) -> float:
        if self.total_trades == 0:
            return 0.0
        return round((self.total_wins / self.total_trades) * 100, 1)

    # ══════════════════════════════════════════════════
    # STATUS — For dashboard and telegram
    # ══════════════════════════════════════════════════
    def get_status(self) -> dict:
        session_pnl = self.current_balance - self.starting_balance
        session_pnl_pct = (
            (session_pnl / self.starting_balance * 100)
            if self.starting_balance > 0 else 0
        )

        recovery_info = None
        if self.recovery_active:
            tier_cfg = ACCOUNT_TIERS.get(self.tier, ACCOUNT_TIERS["MICRO"])
            recovery_info = {
                "active": True,
                "step": self.recovery_step + 1,
                "max_steps": tier_cfg["max_recovery"],
                "accumulated_loss": round(self.recovery_accumulated, 2),
            }

        compound_info = None
        if self.compound_active:
            compound_info = {
                "active": True,
                "multiplier": round(self.compound_multiplier, 2),
                "streak": self.consecutive_wins,
            }

        cooldown_remaining = max(0, int(self.cooldown_until - time.time()))

        return {
            "balance": round(self.current_balance, 2),
            "starting_balance": round(self.starting_balance, 2),
            "session_pnl": round(session_pnl, 2),
            "session_pnl_pct": round(session_pnl_pct, 2),
            "session_high": round(self.session_high, 2),
            "session_low": round(self.session_low, 2),
            "max_drawdown": round(self.max_drawdown, 2),
            "tier": self.tier,
            "total_trades": self.total_trades,
            "total_wins": self.total_wins,
            "total_losses": self.total_losses,
            "win_rate": self.get_win_rate(),
            "consecutive_wins": self.consecutive_wins,
            "consecutive_losses": self.consecutive_losses,
            "best_streak": self.best_streak,
            "worst_streak": self.worst_streak,
            "daily_pnl": round(self.daily_pnl, 2),
            "mode": self._get_mode_display(),
            "recovery": recovery_info,
            "compound": compound_info,
            "locked": self.locked,
            "lock_reason": self.lock_reason,
            "cooldown": cooldown_remaining,
        }

    def _get_mode_display(self) -> str:
        if self.locked:
            return "🔒 LOCKED"
        if time.time() < self.cooldown_until:
            return "⏸️ COOLDOWN"
        if self.recovery_active:
            tier_cfg = ACCOUNT_TIERS.get(self.tier, ACCOUNT_TIERS["MICRO"])
            return f"🔄 RECOVERY {self.recovery_step+1}/{tier_cfg['max_recovery']}"
        if self.compound_active:
            return f"🚀 COMPOUND {self.compound_multiplier:.1f}x"
        return "⚪ NORMAL"

    def get_daily_summary(self) -> dict:
        return {
            "starting": round(self.starting_balance, 2),
            "ending": round(self.current_balance, 2),
            "pnl": round(self.daily_pnl, 2),
            "pnl_pct": round(
                self.daily_pnl / self.starting_balance * 100
                if self.starting_balance > 0 else 0, 2
            ),
            "total_trades": self.total_trades,
            "wins": self.total_wins,
            "losses": self.total_losses,
            "win_rate": self.get_win_rate(),
            "best_streak": self.best_streak,
            "worst_streak": self.worst_streak,
            "max_drawdown": round(self.max_drawdown, 2),
            "recovery_events": len(self.recovery_history),
        }

    def get_equity_curve(self) -> list:
        """Returns balance history for performance chart"""
        curve = []
        running_balance = self.starting_balance

        for trade in self.trade_history:
            curve.append({
                "time": trade["time"],
                "balance": trade["balance"],
            })

        if not curve:
            curve.append({
                "time": time.time(),
                "balance": self.current_balance
            })

        return curve