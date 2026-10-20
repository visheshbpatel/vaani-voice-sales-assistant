import asyncio
import logging
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, WebSocket
from sqlalchemy.exc import SQLAlchemyError
from starlette.websockets import WebSocketDisconnect, WebSocketDisconnected, WebSocketState

from app.database import SessionLocal
from app.models.call import Call
from app.routes.calls import create_call, finalize_call, generate_call_summary
from app.services.gemini_live import run_live_bridge

router = APIRouter()
logger = logging.getLogger(__name__)


def _merge_transcript_text(previous: str, fragment: str) -> str:
    next_text = fragment.strip()
    if not previous:
        return next_text
    if not next_text or previous == next_text or previous.endswith(next_text):
        return previous
    if next_text.startswith(previous):
        return next_text
    max_overlap = min(len(previous), len(next_text))
    for length in range(max_overlap, 0, -1):
        if previous[-length:].lower() == next_text[:length].lower():
            return f"{previous}{next_text[length:]}".strip()
    return f"{previous} {next_text}".strip()

_lock = asyncio.Lock()
_busy = False
_summary_tasks: set[asyncio.Task[None]] = set()


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


async def _generate_summary_in_background(call_id: int) -> None:
    def generate() -> None:
        with SessionLocal() as db:
            call = db.get(Call, call_id)
            if call is not None:
                generate_call_summary(db, call)

    try:
        await asyncio.to_thread(generate)
    except Exception:
        logger.exception("Could not generate call summary.")


def _retain_summary_task(task: asyncio.Task[None]) -> None:
    _summary_tasks.add(task)
    task.add_done_callback(_summary_tasks.discard)


def _create_call() -> Call:
    with SessionLocal() as db:
        return create_call(db)


def _finalize_call(call_id: int, transcript: list[dict[str, Any]]) -> None:
    with SessionLocal() as db:
        finalize_call(db, call_id, transcript)


async def _close_quietly(websocket: WebSocket, code: int = 1000) -> None:
    if websocket.client_state == WebSocketState.CONNECTED:
        try:
            await websocket.close(code=code)
        except (WebSocketDisconnect, WebSocketDisconnected, asyncio.CancelledError):
            pass


async def _send_quietly(websocket: WebSocket, message: dict[str, Any]) -> None:
    if websocket.client_state != WebSocketState.CONNECTED:
        return
    try:
        await websocket.send_json(message)
    except (WebSocketDisconnect, WebSocketDisconnected, asyncio.CancelledError):
        pass


@router.websocket("/ws/voice")
async def voice_socket(websocket: WebSocket) -> None:
    await websocket.accept()
    if not await try_acquire_call():
        await websocket.send_json(
            {"type": "error", "message": "A call is already in progress."}
        )
        await _close_quietly(websocket, code=1008)
        return
    call_id: int | None = None
    transcript: list[dict[str, Any]] = []
    active_entry: dict[str, Any] | None = None
    persistence_error: str | None = None

    def collect_transcript(speaker: str, text: str, finished: bool) -> None:
        nonlocal active_entry
        if active_entry is None or active_entry["speaker"] != speaker:
            active_entry = {
                "speaker": speaker,
                "text": text.strip(),
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }
            transcript.append(active_entry)
        else:
            active_entry["text"] = _merge_transcript_text(
                active_entry["text"], text
            )
        if finished:
            active_entry = None

    async def record_consent(value: bool) -> None:
        nonlocal persistence_error
        if call_id is None:
            return
        def save() -> None:
            with SessionLocal() as db:
                call = db.get(Call, call_id)
                if call is None:
                    return
                call.recording_consent = value
                db.commit()
        try:
            await asyncio.to_thread(save)
        except SQLAlchemyError:
            persistence_error = (
                "Recording consent could not be saved; the recording will not be stored."
            )
            logger.exception("Could not save recording consent.")

    try:
        await websocket.send_json({"type": "status", "value": "connecting"})
        try:
            call = await asyncio.to_thread(_create_call)
            call_id = call.id
            await _send_quietly(
                websocket, {"type": "call_started", "call_id": call_id}
            )
        except SQLAlchemyError:
            logger.exception("Could not create call history record.")
            persistence_error = (
                "Call history is unavailable; the call can continue without saving."
            )
        await run_live_bridge(
            websocket,
            collect_transcript,
            send_connecting=False,
            on_recording_consent=record_consent,
        )
    except WebSocketDisconnect:
        pass
    except Exception:
        logger.exception("Voice WebSocket bridge failed")
    finally:
        try:
            if call_id is not None:
                try:
                    await asyncio.to_thread(_finalize_call, call_id, transcript)
                except SQLAlchemyError:
                    logger.exception("Could not finalize call history record.")
                    await _send_quietly(
                        websocket,
                        {
                            "type": "error",
                            "message": "The call ended, but its history could not be saved.",
                            "fatal": False,
                        },
                    )
            if persistence_error:
                await _send_quietly(
                    websocket,
                    {"type": "error", "message": persistence_error, "fatal": False},
                )
        finally:
            await release_call()
            await _close_quietly(websocket)
            if call_id is not None:
                _retain_summary_task(
                    asyncio.create_task(_generate_summary_in_background(call_id))
                )
