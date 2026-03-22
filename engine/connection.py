"""
═══════════════════════════════════════════════════════════════
  DERIV API CONNECTION — Self-healing WebSocket
═══════════════════════════════════════════════════════════════
"""
import asyncio
import json
import time
import websockets
from collections import defaultdict
from config import DERIV_WS_URL, DERIV_APP_ID, DERIV_API_TOKEN, DATA


class DerivAPI:

    def __init__(self, app_id=None, token=None):
        self.app_id = app_id or DERIV_APP_ID
        self.token = token or DERIV_API_TOKEN
        self.url = DERIV_WS_URL.format(self.app_id)
        self.ws = None
        self.connected = False
        self.req_id = 0
        self.pending = {}
        self.callbacks = defaultdict(list)
        self.subscriptions = {}
        self.balance = 0.0
        self.currency = "USD"
        self.account_info = {}
        self._reconnect_count = 0
        self._listen_task = None
        self._lock = asyncio.Lock()

    # ──────────────────────────────────────────────────
    # CONNECTION
    # ──────────────────────────────────────────────────
    async def connect(self):
        while self._reconnect_count < 50:
            try:
                self.ws = await websockets.connect(
                    self.url,
                    ping_interval=25,
                    ping_timeout=15,
                    close_timeout=5,
                    max_size=2**22
                )
                self.connected = True
                self._reconnect_count = 0

                # Authenticate
                auth = await self._send({"authorize": self.token})
                if "error" in auth:
                    raise Exception(f"Auth failed: {auth['error']['message']}")

                self.account_info = auth["authorize"]
                self.balance = float(self.account_info["balance"])
                self.currency = self.account_info.get("currency", "USD")

                print(f"✅ Deriv connected | {self.currency} {self.balance:.2f}")

                # Subscribe to balance stream
                await self._send({"balance": 1, "subscribe": 1})

                # Restore any previous subscriptions
                await self._restore_subs()

                # Start listener
                self._listen_task = asyncio.create_task(self._listen())
                return True

            except Exception as e:
                self._reconnect_count += 1
                wait = min(2 ** self._reconnect_count, 30)
                print(f"⚠️ Connection error: {e}. Retry in {wait}s "
                      f"({self._reconnect_count}/50)")
                await asyncio.sleep(wait)

        print("❌ Max reconnection attempts reached")
        return False

    async def disconnect(self):
        self.connected = False
        if self._listen_task:
            self._listen_task.cancel()
        if self.ws:
            await self.ws.close()

    # ──────────────────────────────────────────────────
    # SEND / RECEIVE
    # ──────────────────────────────────────────────────
    async def _send(self, request: dict) -> dict:
        async with self._lock:
            self.req_id += 1
            request["req_id"] = self.req_id
            fut = asyncio.get_running_loop().create_future()
            self.pending[self.req_id] = fut
            await self.ws.send(json.dumps(request))

        try:
            return await asyncio.wait_for(fut, timeout=15)
        except asyncio.TimeoutError:
            self.pending.pop(self.req_id, None)
            return {"error": {"message": "Request timed out"}}

    async def _listen(self):
        try:
            async for raw in self.ws:
                msg = json.loads(raw)
                rid = msg.get("req_id")

                # Resolve pending requests
                if rid and rid in self.pending:
                    if not self.pending[rid].done():
                        self.pending[rid].set_result(msg)
                    self.pending.pop(rid, None)

                # Route tick stream
                if "tick" in msg:
                    sym = msg["tick"]["symbol"]
                    for cb in self.callbacks.get(f"tick_{sym}", []):
                        asyncio.create_task(cb(msg["tick"]))

                # Route balance updates
                if "balance" in msg:
                    self.balance = float(msg["balance"]["balance"])
                    for cb in self.callbacks.get("balance", []):
                        asyncio.create_task(cb(self.balance))

                # Route proposal open contract (trade result)
                if "proposal_open_contract" in msg:
                    for cb in self.callbacks.get("contract_update", []):
                        asyncio.create_task(cb(msg["proposal_open_contract"]))

        except websockets.exceptions.ConnectionClosed:
            print("⚠️ WebSocket closed, reconnecting...")
            self.connected = False
            await self.connect()

    # ──────────────────────────────────────────────────
    # DATA RETRIEVAL
    # ──────────────────────────────────────────────────
    async def get_ticks_history(self, symbol: str, count: int = 5000) -> list:
        resp = await self._send({
            "ticks_history": symbol,
            "adjust_start_time": 1,
            "count": min(count, 5000),
            "end": "latest",
            "start": 1,
            "style": "ticks"
        })
        if "history" in resp:
            prices = resp["history"]["prices"]
            times = resp["history"]["times"]
            return [{"price": float(p), "time": int(t)}
                    for p, t in zip(prices, times)]
        return []

    async def get_candles(self, symbol: str, granularity: int,
                          count: int = 100) -> list:
        resp = await self._send({
            "ticks_history": symbol,
            "adjust_start_time": 1,
            "count": count,
            "end": "latest",
            "start": 1,
            "style": "candles",
            "granularity": granularity
        })
        if "candles" in resp:
            return [{
                "open":  float(c["open"]),
                "high":  float(c["high"]),
                "low":   float(c["low"]),
                "close": float(c["close"]),
                "time":  int(c["epoch"])
            } for c in resp["candles"]]
        return []

    # ──────────────────────────────────────────────────
    # SUBSCRIPTIONS
    # ──────────────────────────────────────────────────
    async def subscribe_ticks(self, symbol: str, callback):
        self.callbacks[f"tick_{symbol}"].append(callback)
        resp = await self._send({"ticks": symbol, "subscribe": 1})
        if "subscription" in resp:
            self.subscriptions[symbol] = resp["subscription"]["id"]
        return resp

    async def _restore_subs(self):
        symbols = list(self.subscriptions.keys())
        self.subscriptions.clear()
        for sym in symbols:
            cbs = self.callbacks.get(f"tick_{sym}", [])
            if cbs:
                resp = await self._send({"ticks": sym, "subscribe": 1})
                if "subscription" in resp:
                    self.subscriptions[sym] = resp["subscription"]["id"]

    # ──────────────────────────────────────────────────
    # TRADING
    # ──────────────────────────────────────────────────
    async def get_proposal(self, params: dict) -> dict:
        return await self._send({"proposal": 1, "subscribe": 0, **params})

    async def buy(self, proposal_id: str, price: float) -> dict:
        return await self._send({"buy": proposal_id, "price": price})

    async def buy_contract(self, params: dict, stake: float) -> dict:
        """Get proposal then buy in one step"""
        proposal = await self.get_proposal(params)

        if "error" in proposal:
            return proposal

        if "proposal" not in proposal:
            return {"error": {"message": "No proposal received"}}

        prop = proposal["proposal"]
        proposal_id = prop["id"]

        result = await self.buy(proposal_id, stake)
        return result

    async def get_balance(self) -> float:
        resp = await self._send({"balance": 1})
        if "balance" in resp:
            self.balance = float(resp["balance"]["balance"])
        return self.balance

    def on_balance_update(self, callback):
        self.callbacks["balance"].append(callback)

    def on_contract_update(self, callback):
        self.callbacks["contract_update"].append(callback)