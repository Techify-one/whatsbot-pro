"""WebSocket endpoint."""

import asyncio
import json

from fastapi import WebSocket, WebSocketDisconnect

from server.auth import auth_required, rbac_enforced, resolve_request_token


def register_routes(app, deps):
    ws_manager = deps.ws_manager
    state = deps.state
    settings = deps.settings

    @app.websocket("/ws")
    async def websocket_endpoint(websocket: WebSocket):
        # Auth via the ?token= query param (the Authorization header isn't usable
        # for WS in the browser). Accept a USER session OR the legacy single-
        # password token (plano 03, additive); when RBAC is enforced, only a user
        # session is valid. Open install (no password, no enforcement) → no check.
        if auth_required(settings) or rbac_enforced(settings):
            token = websocket.query_params.get("token", "")
            kind, _user = await asyncio.to_thread(resolve_request_token, token, settings)
            ok = (kind == "user") if rbac_enforced(settings) else (kind is not None)
            if not ok:
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
