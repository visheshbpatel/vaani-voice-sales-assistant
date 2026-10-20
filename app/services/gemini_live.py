import asyncio
import base64
import inspect
import json
import logging
import time
from collections.abc import Callable
from typing import Any

from google import genai
from google.genai import types
from starlette.websockets import WebSocketDisconnect, WebSocketDisconnected

from app.config import settings
from app.services.product_knowledge import (
    GREETING_TRIGGER,
    IDLE_NUDGE_TRIGGER,
    system_instruction,
)

LIVE_MODEL = "gemini-3.8-live"
INPUT_MIME = "audio/pcm;rate=16000"
IDLE_NUDGE_SECONDS = 8.0
MAX_CONSECUTIVE_NUDGES = 2
CLOSING_PHRASES = ("goodbye", "you can end the call", "end the call")
logger = logging.getLogger(__name__)


def _is_closing_text(text: str) -> bool:
    lowered = text.lower()
    return any(phrase in lowered for phrase in CLOSING_PHRASES)


def live_config() -> dict[str, Any]:
    # Setup field names from Live capabilities (snake_case in the Python SDK).
    return {
        "response_modalities": ["AUDIO"],
        "speech_config": {"language_code": "en-IN"},
        "system_instruction": system_instruction(),
        "input_audio_transcription": {},
        "output_audio_transcription": {},
        "realtime_input_config": {
            "automatic_activity_detection": {
                "start_of_speech_sensitivity": "START_SENSITIVITY_LOW",
                "end_of_speech_sensitivity": "END_SENSITIVITY_HIGH",
                "prefix_padding_ms": 300,
                "silence_duration_ms": 700,
            }
        },
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


async def _browser_to_gemini(
    websocket,
    session,
    stop: asyncio.Event,
    playback_idle: asyncio.Event,
    caller_spoke: asyncio.Event,
    timing: dict[str, float | None],
    on_recording_consent=None,
    on_lead_details=None,
) -> str:
    try:
        while not stop.is_set():
            message = await websocket.receive()
            if message["type"] == "websocket.disconnect":
                return "client_disconnect"
            if message.get("text"):
                try:
                    payload = json.loads(message["text"])
                except json.JSONDecodeError:
                    continue
                if payload.get("type") == "end":
                    return "client_disconnect"
                if payload.get("type") == "recording_consent" and on_recording_consent:
                    result = on_recording_consent(bool(payload.get("value")))
                    if inspect.isawaitable(result):
                        await result
                if payload.get("type") == "lead_details" and on_lead_details:
                    on_lead_details(payload.get("value"))
                if payload.get("type") == "playback_idle":
                    playback_idle.set()
                continue
            chunk = message.get("bytes")
            if not chunk:
                continue
            caller_spoke.set()
            await session.send_realtime_input(
                audio=types.Blob(data=chunk, mime_type=INPUT_MIME)
            )
            timing["last_mic_sent_at"] = time.monotonic()
    except WebSocketDisconnect:
        return "client_disconnect"
    finally:
        stop.set()
    return "client_disconnect"


async def _gemini_to_browser(
    websocket,
    session,
    stop: asyncio.Event,
    transcript_seen: asyncio.Event,
    playback_idle: asyncio.Event,
    caller_spoke: asyncio.Event,
    timing: dict[str, float | None],
    on_transcript: Callable[[str, str, bool], None] | None = None,
) -> str:
    nudge_task = None
    nudge_state = {"count": 0}
    nudging_disabled = False
    try:
        while True:
            async for response in session.receive():
                if stop.is_set():
                    return "client_disconnect"
                if getattr(response, "go_away", None) is not None:
                    await websocket.send_json(
                        {
                            "type": "error",
                            "message": "The live session reached its time limit.",
                        }
                    )
                    return "time_limit"
                content = getattr(response, "server_content", None)
                if not content:
                    continue
                if content.interrupted is True:
                    if nudge_state.get("last_sent_at") is not None:
                        logger.info(
                            "Live response interrupted; seconds since nudge: %.2f",
                            time.monotonic() - nudge_state["last_sent_at"],
                        )
                    else:
                        logger.info("Live response interrupted")
                    await websocket.send_json({"type": "interrupted"})
                    if nudge_task and not nudge_task.done():
                        nudge_task.cancel()
                        await asyncio.gather(nudge_task, return_exceptions=True)
                    continue
                if getattr(content, "turn_complete", None) is True:
                    if nudge_task and not nudge_task.done():
                        nudge_task.cancel()
                        await asyncio.gather(nudge_task, return_exceptions=True)
                for transcription, speaker in (
                    (getattr(content, "input_transcription", None), "customer"),
                    (getattr(content, "output_transcription", None), "assistant"),
                ):
                    text = getattr(transcription, "text", None) if transcription else None
                    if text:
                        transcript_seen.set()
                        if on_transcript:
                            on_transcript(
                                speaker,
                                text,
                                bool(getattr(transcription, "finished", False)),
                            )
                        if speaker == "customer":
                            caller_spoke.set()
                            nudge_state["count"] = 0
                            if nudge_task and not nudge_task.done():
                                nudge_task.cancel()
                                await asyncio.gather(
                                    nudge_task, return_exceptions=True
                                )
                        await websocket.send_json(
                            {
                                "type": "transcript",
                                "speaker": speaker,
                                "text": text,
                                "partial": not bool(
                                    getattr(transcription, "finished", False)
                                ),
                            }
                        )
                        if speaker == "assistant" and _is_closing_text(text):
                            nudging_disabled = True
                if getattr(content, "model_turn", None):
                    for part in content.model_turn.parts or []:
                        inline = getattr(part, "inline_data", None)
                        pcm = _pcm_bytes(
                            getattr(inline, "data", None) if inline else None
                        )
                        if not pcm:
                            continue
                        if (
                            timing["last_mic_sent_at"] is not None
                            and not timing["response_logged"]
                        ):
                            latency_ms = (
                                time.monotonic() - timing["last_mic_sent_at"]
                            ) * 1000
                            logger.info(
                                "response_latency_ms=%.0f", latency_ms
                            )
                            timing["response_logged"] = True
                        await websocket.send_json(
                            {"type": "status", "value": "speaking"}
                        )
                        await websocket.send_bytes(pcm)
                if getattr(content, "generation_complete", None) is True:
                    timing["response_logged"] = False
                    await websocket.send_json(
                        {"type": "status", "value": "listening"}
                    )
                    caller_spoke.clear()
                    playback_idle.clear()
                    if not nudging_disabled:
                        if nudge_task and not nudge_task.done():
                            nudge_task.cancel()
                        nudge_task = asyncio.create_task(
                            _idle_nudge_loop(
                                session,
                                stop,
                                playback_idle,
                                caller_spoke,
                                nudge_state,
                                lambda: nudging_disabled,
                            )
                        )
    except WebSocketDisconnect:
        return "client_disconnect"
    except Exception:
        logger.exception("Live session receive failed")
        await websocket.send_json(
            {"type": "error", "message": "The live session could not continue."}
        )
        return "error"
    finally:
        if nudge_task and not nudge_task.done():
            nudge_task.cancel()
            await asyncio.gather(nudge_task, return_exceptions=True)
        stop.set()
    return "session_closed"

async def _idle_nudge_loop(
    session,
    stop: asyncio.Event,
    playback_idle: asyncio.Event,
    caller_spoke: asyncio.Event,
    nudge_state,
    is_disabled,
) -> None:
    if stop.is_set():
        return
    try:
        playback_wait = asyncio.create_task(playback_idle.wait())
        caller_wait = asyncio.create_task(caller_spoke.wait())
        done, pending = await asyncio.wait(
            {playback_wait, caller_wait},
            return_when=asyncio.FIRST_COMPLETED,
        )
        for task in pending:
            task.cancel()
        await asyncio.gather(*pending, return_exceptions=True)
        if caller_wait in done:
            return
        if stop.is_set():
            return
        await asyncio.wait_for(caller_spoke.wait(), timeout=IDLE_NUDGE_SECONDS)
    except asyncio.TimeoutError:
        if (
            not stop.is_set()
            and nudge_state["count"] < MAX_CONSECUTIVE_NUDGES
            and not is_disabled()
        ):
            await session.send_realtime_input(text=IDLE_NUDGE_TRIGGER)
            nudge_state["count"] += 1
            nudge_state["last_sent_at"] = time.monotonic()
            logger.info("Idle nudge sent")


async def _send_duration(
    websocket, started_at: float, stop: asyncio.Event, final: bool = False
) -> None:
    await websocket.send_json(
        {
            "type": "duration",
            "seconds": max(0, int(time.monotonic() - started_at)),
            "final": final,
        }
    )


async def run_live_bridge(
    websocket,
    on_transcript=None,
    send_connecting: bool = True,
    on_recording_consent=None,
    on_lead_details=None,
) -> None:
    if not settings.GEMINI_API_KEY:
        await websocket.send_json(
            {"type": "error", "message": "Server is missing GEMINI_API_KEY."}
        )
        return
    if send_connecting:
        await websocket.send_json({"type": "status", "value": "connecting"})
    stop = asyncio.Event()
    started_at = time.monotonic()
    transcript_seen = asyncio.Event()
    reason = "error"
    try:
        async with live_connect(settings.GEMINI_API_KEY, live_config()) as session:
            await session.send_realtime_input(text=GREETING_TRIGGER)
            await websocket.send_json({"type": "status", "value": "listening"})
            playback_idle = asyncio.Event()
            caller_spoke = asyncio.Event()
            timing = {
                "last_mic_sent_at": None,
                "response_logged": False,
            }
            sender = asyncio.create_task(
                _browser_to_gemini(
                    websocket,
                    session,
                    stop,
                    playback_idle,
                    caller_spoke,
                    timing,
                    on_recording_consent,
                    on_lead_details,
                )
            )
            receiver = asyncio.create_task(
                _gemini_to_browser(
                    websocket,
                    session,
                    stop,
                    transcript_seen,
                    playback_idle,
                    caller_spoke,
                    timing,
                    on_transcript,
                )
            )
            duration_task = asyncio.create_task(
                _duration_loop(websocket, started_at, stop)
            )
            done, pending = await asyncio.wait(
                {sender, receiver},
                return_when=asyncio.FIRST_COMPLETED,
            )
            reason = next(iter(done)).result()
            stop.set()
            for task in pending:
                task.cancel()
            await asyncio.gather(sender, receiver, return_exceptions=True)
            duration_task.cancel()
            await asyncio.gather(duration_task, return_exceptions=True)
            if reason != "client_disconnect":
                await _send_duration(websocket, started_at, stop, final=True)
            if reason != "client_disconnect" and not transcript_seen.is_set():
                await websocket.send_json(
                    {
                        "type": "transcript_unavailable",
                        "message": "Live transcription was unavailable for this call.",
                    }
                )
    except (WebSocketDisconnect, WebSocketDisconnected):
        return
    except Exception:
        await websocket.send_json(
            {"type": "error", "message": "Could not start or keep the voice session."}
        )
        logger.exception("Live bridge ended with reason: error")
        raise
    else:
        logger.info("Live bridge ended with reason: %s", reason)


async def _duration_loop(
    websocket, started_at: float, stop: asyncio.Event
) -> None:
    while not stop.is_set():
        try:
            await asyncio.wait_for(stop.wait(), timeout=1)
        except asyncio.TimeoutError:
            if not stop.is_set():
                await _send_duration(websocket, started_at, stop)
