import asyncio
import base64
from typing import Any

from google import genai
from google.genai import types
from starlette.websockets import WebSocketDisconnect

from app.config import settings
from app.services.product_knowledge import system_instruction

LIVE_MODEL = "gemini-3.8-live"
INPUT_MIME = "audio/pcm;rate=16000"


def live_config() -> dict[str, Any]:
    # Setup field names from Live capabilities (snake_case in the Python SDK).
    return {
        "response_modalities": ["AUDIO"],
        "system_instruction": system_instruction(),
        "input_audio_transcription": {},
        "output_audio_transcription": {},
    }


def live_connect(api_key: str, config: dict[str, Any]):
    client = genai.Client(api_key=api_key)
    return client.aio.live.connect(model=LIVE_MODEL, config=config)


def _pcm_bytes(data: Any) -> bytes | None:
    if data is None:
        return None
    if isinstance(data, str):
        return base64.b64decode(data)
    return bytes(data)


async def _browser_to_gemini(websocket, session, stop: asyncio.Event) -> None:
    try:
        while not stop.is_set():
            message = await websocket.receive()
            if message["type"] == "websocket.disconnect":
                break
            chunk = message.get("bytes")
            if not chunk:
                continue
            await session.send_realtime_input(
                audio=types.Blob(data=chunk, mime_type=INPUT_MIME)
            )
    except WebSocketDisconnect:
        pass
    finally:
        stop.set()


async def _gemini_to_browser(websocket, session, stop: asyncio.Event) -> None:
    try:
        async for response in session.receive():
            if stop.is_set():
                break
            if getattr(response, "go_away", None) is not None:
                await websocket.send_json(
                    {"type": "error", "message": "The live session is about to close."}
                )
                break
            content = getattr(response, "server_content", None)
            if not content:
                continue
            if content.interrupted is True:
                await websocket.send_json({"type": "interrupted"})
                continue
            if getattr(content, "model_turn", None):
                for part in content.model_turn.parts or []:
                    inline = getattr(part, "inline_data", None)
                    pcm = _pcm_bytes(getattr(inline, "data", None) if inline else None)
                    if not pcm:
                        continue
                    await websocket.send_json({"type": "status", "value": "speaking"})
                    await websocket.send_bytes(pcm)
            if getattr(content, "generation_complete", None) is True:
                await websocket.send_json({"type": "status", "value": "listening"})
    except WebSocketDisconnect:
        pass
    finally:
        stop.set()


async def run_live_bridge(websocket) -> None:
    if not settings.GEMINI_API_KEY:
        await websocket.send_json(
            {"type": "error", "message": "Server is missing GEMINI_API_KEY."}
        )
        return
    await websocket.send_json({"type": "status", "value": "connecting"})
    stop = asyncio.Event()
    try:
        async with live_connect(settings.GEMINI_API_KEY, live_config()) as session:
            await websocket.send_json({"type": "status", "value": "listening"})
            sender = asyncio.create_task(_browser_to_gemini(websocket, session, stop))
            receiver = asyncio.create_task(_gemini_to_browser(websocket, session, stop))
            _done, pending = await asyncio.wait(
                {sender, receiver},
                return_when=asyncio.FIRST_COMPLETED,
            )
            stop.set()
            for task in pending:
                task.cancel()
            await asyncio.gather(sender, receiver, return_exceptions=True)
    except Exception:
        await websocket.send_json(
            {"type": "error", "message": "Could not start or keep the voice session."}
        )
        raise
