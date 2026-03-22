"""
═══════════════════════════════════════════════════════════════
  DASHBOARD SERVER — FastAPI + WebSocket real-time updates
  Serves the UI and pushes live data to all connected clients.
═══════════════════════════════════════════════════════════════
"""
import asyncio
import json
import time
import uvicorn

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse, JSONResponse
from pathlib import Path

from config import DASHBOARD_HOST, DASHBOARD_PORT

app = FastAPI(title="Deriv Smart Trader", docs_url=None, redoc_url=None)


class DashboardManager:
    """Manages WebSocket connections and data broadcasting"""

    def __init__(self):
        self.connections: list[WebSocket] = []
        self.trader = None  # Set after import

    async def connect(self, ws: WebSocket):
        await ws.accept()
        self.connections.append(ws)

        # Send full state on connect
        if self.trader:
            try:
                await ws.send_json({
                    "type": "snapshot",
                    "data": self.trader.get_full_state()
                })
            except Exception:
                pass

    def disconnect(self, ws: WebSocket):
        if ws in self.connections:
            self.connections.remove(ws)

    async def broadcast(self, msg_type: str, data):
        if not self.connections:
            return

        payload = json.dumps({"type": msg_type, "data": data}, default=str)
        dead = []

        for ws in self.connections:
            try:
                await ws.send_text(payload)
            except Exception:
                dead.append(ws)

        for ws in dead:
            self.connections.remove(ws)

    # Push methods called from main.py
    async def push_tick(self, symbol, price, tick_analysis):
        await self.broadcast("tick", {
            "symbol": symbol,
            "price": price,
            "tmi": tick_analysis.get("tmi", 50),
            "acceleration": tick_analysis.get("acceleration", 0),
            "ts": time.time()
        })

    async def push_scanner(self, scanner_data):
        await self.broadcast("scanner", scanner_data)

    async def push_trade(self, trade_data):
        await self.broadcast("trade", trade_data)

    async def push_account(self, account_data):
        await self.broadcast("account", account_data)

    async def push_recovery(self, recovery_data):
        await self.broadcast("recovery", recovery_data)

    async def push_performance(self, equity_data):
        await self.broadcast("performance", equity_data)

    async def push_gates(self, gates_data):
        await self.broadcast("gates", gates_data)


dm = DashboardManager()


# ══════════════════════════════════════════════
# ROUTES
# ══════════════════════════════════════════════
@app.get("/", response_class=HTMLResponse)
async def index():
    html_path = Path(__file__).parent / "templates" / "index.html"
    return HTMLResponse(content=html_path.read_text(), status_code=200)


@app.websocket("/ws")
async def websocket_endpoint(ws: WebSocket):
    await dm.connect(ws)
    try:
        while True:
            data = await ws.receive_json()
            action = data.get("action", "")

            if action == "get_state" and dm.trader:
                await ws.send_json({
                    "type": "snapshot",
                    "data": dm.trader.get_full_state()
                })

            elif action == "update_settings" and dm.trader:
                dm.trader.update_settings(data.get("settings", {}))
                await ws.send_json({
                    "type": "settings_updated",
                    "data": {"ok": True}
                })

            elif action == "unlock" and dm.trader:
                dm.trader.risk.unlock()
                await ws.send_json({
                    "type": "account",
                    "data": dm.trader.risk.get_status()
                })

            elif action == "pause" and dm.trader:
                dm.trader.running = not dm.trader.running
                await ws.send_json({
                    "type": "status",
                    "data": {"running": dm.trader.running}
                })

    except WebSocketDisconnect:
        dm.disconnect(ws)
    except Exception:
        dm.disconnect(ws)


@app.get("/api/state")
async def api_state():
    if dm.trader:
        return JSONResponse(dm.trader.get_full_state())
    return JSONResponse({"error": "Not initialized"})


@app.get("/api/trades")
async def api_trades():
    if dm.trader:
        return JSONResponse(dm.trader.executor.get_trade_history(100))
    return JSONResponse([])


@app.get("/api/stats")
async def api_stats():
    if dm.trader:
        return JSONResponse(dm.trader.executor.get_stats())
    return JSONResponse({})


# ══════════════════════════════════════════════
# STARTUP
# ══════════════════════════════════════════════
def start_all():
    """Start dashboard server + trading bot together"""
    import main as main_module

    dm.trader = main_module.trader
    main_module.trader.dashboard = dm

    async def run():
        config = uvicorn.Config(
            app, host=DASHBOARD_HOST, port=DASHBOARD_PORT,
            log_level="warning"
        )
        server = uvicorn.Server(config)

        # Start bot
        bot_task = asyncio.create_task(main_module.trader.start())

        # Start server
        await server.serve()

    asyncio.run(run())