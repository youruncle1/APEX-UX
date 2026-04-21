import json
from uuid import UUID

from fastapi import WebSocket


# All active WebSocket connections keyed by evaluation ID, one evaluation can have multiple clients
active_connections: dict[UUID, list[WebSocket]] = {}


async def broadcast_progress(evaluation_id: UUID, message: dict):
    """
    Broadcast a progress event to all clients connected to a given evaluation.

    Args:
        evaluation_id: UUID of the evaluation to broadcast to.
        message: Event payload to send as JSON.
    """
    if evaluation_id not in active_connections:
        return

    message_json = json.dumps(message)

    dead_connections = []
    for websocket in active_connections[evaluation_id]:
        try:
            await websocket.send_text(message_json)
        except Exception:
            # Connection dropped without a clean disconnect event
            dead_connections.append(websocket)

    for ws in dead_connections:
        active_connections[evaluation_id].remove(ws)
