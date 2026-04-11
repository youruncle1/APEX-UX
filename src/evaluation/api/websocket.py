import json
from uuid import UUID

from fastapi import WebSocket


# WebSocket connections for live progress updates
active_connections: dict[UUID, list[WebSocket]] = {}


async def broadcast_progress(evaluation_id: UUID, message: dict):
    if evaluation_id not in active_connections:
        return

    message_json = json.dumps(message)

    dead_connections = []
    for websocket in active_connections[evaluation_id]:
        try:
            await websocket.send_text(message_json)
        except Exception:
            dead_connections.append(websocket)

    for ws in dead_connections:
        active_connections[evaluation_id].remove(ws)