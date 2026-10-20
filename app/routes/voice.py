import asyncio
import logging

from fastapi import APIRouter, WebSocket
from starlette.websockets import WebSocketDisconnect, WebSocketState

from app.services.gemini_live import run_live_bridge

router = APIRouter()
logger = logging.getLogger(__name__)

_lock = asyncio.Lock()
_busy = False


async def try_acquire_call() -> bool:
    global _busy
    async with _lock:
        if _busy:
            return False
        _busy = True
        return True


async def release_call() -> None:
    global _busy
    async with _lock:
        _busy = False


def reset_call_gate() -> None:
    global _busy
    _busy = False


async def _close_quietly(websocket: WebSocket, code: int = 1000) -> None:
    if websocket.client_state == WebSocketState.CONNECTED:
        await websocket.close(code=code)


@router.websocket("/ws/voice")
async def voice_socket(websocket: WebSocket) -> None:
    await websocket.accept()
    if not await try_acquire_call():
        await websocket.send_json(
            {"type": "error", "message": "A call is already in progress."}
        )
        await _close_quietly(websocket, code=1008)
        return
    try:
        await run_live_bridge(websocket)
    except WebSocketDisconnect:
        pass
    except Exception:
        logger.exception("Voice WebSocket bridge failed")
    finally:
        await release_call()
        await _close_quietly(websocket)
