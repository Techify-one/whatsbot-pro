"""WebSocket endpoint."""

import asyncio
import json

from fastapi import WebSocket, WebSocketDisconnect

from server.auth import rbac_enforced, resolve_request_token
from db.repositories import user_repo


def register_routes(app, deps):
    ws_manager = deps.ws_manager
    state = deps.state
    settings = deps.settings

    @app.websocket("/ws")
    async def websocket_endpoint(websocket: WebSocket):
        # Auth via the ?token= query param (the Authorization header isn't usable
        # for WS in the browser). The gate closes as soon as ≥1 user exists
        # (``has_users`` — same self-healing rule as the HTTP middleware, plano 48):
        # a USER session is then required. Genuinely zero-user install → no check.
        has_users = await asyncio.to_thread(user_repo.has_any)
        enforce = rbac_enforced(settings) or has_users
        if enforce:
            token = websocket.query_params.get("token", "")
            kind, _user = await asyncio.to_thread(resolve_request_token, token)
            if kind != "user":
                await websocket.accept()
                await websocket.close(code=4401, reason="Unauthorized")
                return

        await ws_manager.connect(websocket)
        # Send initial state
        try:
            await websocket.send_text(json.dumps({"event": "status", "data": {
                "connected": state.connected,
                "msg_count": state.msg_count,
                "auto_reply_running": state.auto_reply_running,
            }}))
            await websocket.send_text(json.dumps({"event": "gowa_status", "data": {
                "message": state.notification,
            }}))
            # Send current QR state so page refreshes show QR immediately
            if not state.connected and state.qr_data:
                await websocket.send_text(json.dumps({"event": "qr_update", "data": {
                    "available": True,
                    "version": state.qr_version,
                }}))
            else:
                await websocket.send_text(json.dumps({"event": "qr_update", "data": {
                    "available": False,
                }}))
        except Exception:
            pass
        # Keep alive
        try:
            while True:
                data = await websocket.receive_text()
                msg = json.loads(data)
                if msg.get("action") == "ping":
                    await websocket.send_text(json.dumps({"event": "pong", "data": {}}))
        except WebSocketDisconnect:
            ws_manager.disconnect(websocket)
        except Exception:
            ws_manager.disconnect(websocket)
