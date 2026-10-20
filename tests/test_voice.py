import asyncio
import time
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.routes import voice as voice_route
from app.services import gemini_live
from app.services.product_knowledge import GREETING_TRIGGER, IDLE_NUDGE_TRIGGER


class FakeSession:
    def __init__(
        self,
        events,
        hang: asyncio.Event | None = None,
        receive_batches=None,
    ):
        self.receive_batches = receive_batches or [events]
        self.receive_index = 0
        self.hang = hang
        self.sent = []
        self.closed = False

    async def send_realtime_input(self, **kwargs):
        self.sent.append(kwargs)

    async def receive(self):
        if self.receive_index < len(self.receive_batches):
            events = self.receive_batches[self.receive_index]
            self.receive_index += 1
        else:
            events = []
        for event in events:
            yield event
        if self.hang is not None and self.receive_index >= len(self.receive_batches):
            await asyncio.sleep(3600)
        else:
            await asyncio.sleep(0)

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        self.closed = True
        if self.hang is not None:
            self.hang.set()
        return False


def _wait_for(predicate, timeout: float = 2.0) -> bool:
    """Poll until predicate() is true. The app runs in another thread under TestClient."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.01)
    return False


def _receive_until(ws, message_type):
    while True:
        message = ws.receive_json()
        if message["type"] == message_type:
            return message


def _audio_event(pcm: bytes):
    part = SimpleNamespace(inline_data=SimpleNamespace(data=pcm))
    content = SimpleNamespace(
        interrupted=False,
        generation_complete=False,
        model_turn=SimpleNamespace(parts=[part]),
    )
    return SimpleNamespace(server_content=content, go_away=None)


def _complete_event():
    content = SimpleNamespace(
        interrupted=False,
        generation_complete=True,
        model_turn=None,
    )
    return SimpleNamespace(server_content=content, go_away=None)


def _interrupt_event():
    content = SimpleNamespace(
        interrupted=True,
        generation_complete=False,
        model_turn=None,
    )
    return SimpleNamespace(server_content=content, go_away=None)


def _transcript_event(
    input_text=None,
    input_finished=False,
    output_text=None,
    output_finished=False,
):
    content = SimpleNamespace(
        interrupted=False,
        generation_complete=False,
        model_turn=None,
        input_transcription=(
            SimpleNamespace(text=input_text, finished=input_finished)
            if input_text is not None
            else None
        ),
        output_transcription=(
            SimpleNamespace(text=output_text, finished=output_finished)
            if output_text is not None
            else None
        ),
    )
    return SimpleNamespace(server_content=content, go_away=None)


@pytest.fixture(autouse=True)
def _reset_gate(monkeypatch):
    voice_route.reset_call_gate()
    monkeypatch.setattr(gemini_live.settings, "GEMINI_API_KEY", "test-key")
    yield
    voice_route.reset_call_gate()


def test_health():
    with TestClient(app) as client:
        response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_live_loop_sends_audio_and_cleans_up(monkeypatch):
    # hang keeps the fake session open, like a real call, until the client disconnects.
    session = FakeSession(
        [_audio_event(b"\x00\x00" * 24), _complete_event()],
        hang=asyncio.Event(),
    )

    def fake_connect(api_key, config):
        assert api_key == "test-key"
        assert config["input_audio_transcription"] == {}
        assert config["output_audio_transcription"] == {}
        assert config["speech_config"] == {"language_code": "en-IN"}
        assert config["realtime_input_config"] == {
            "automatic_activity_detection": {
                "start_of_speech_sensitivity": "START_SENSITIVITY_LOW",
                "end_of_speech_sensitivity": "END_SENSITIVITY_LOW",
                "prefix_padding_ms": 300,
                "silence_duration_ms": 1200,
            }
        }
        return session

    monkeypatch.setattr(gemini_live, "live_connect", fake_connect)

    with TestClient(app) as client:
        with client.websocket_connect("/ws/voice") as ws:
            assert ws.receive_json() == {"type": "status", "value": "connecting"}
            assert ws.receive_json() == {"type": "status", "value": "listening"}
            ws.send_bytes(b"\x00\x00" * 160)
            assert ws.receive_json() == {"type": "status", "value": "speaking"}
            assert ws.receive_bytes() == b"\x00\x00" * 24
            assert ws.receive_json() == {"type": "status", "value": "listening"}
            # Wait until the server has forwarded the client's audio to the session,
            # before closing the socket (otherwise the check below can race).
            assert _wait_for(lambda: session.sent), "client audio was never forwarded"

    assert session.closed is True
    assert session.sent[0] == {"text": GREETING_TRIGGER}
    assert any(item.get("audio") is not None for item in session.sent[1:])


def test_interrupt_is_forwarded(monkeypatch):
    session = FakeSession([_interrupt_event()])
    monkeypatch.setattr(gemini_live, "live_connect", lambda api_key, config: session)

    with TestClient(app) as client:
        with client.websocket_connect("/ws/voice") as ws:
            ws.receive_json()
            ws.receive_json()
            assert ws.receive_json() == {"type": "interrupted"}


def test_transcript_forwards_partial_and_final_turns(monkeypatch):
    session = FakeSession(
        [
            _transcript_event(input_text="hel", input_finished=False),
            _transcript_event(input_text="hello", input_finished=True),
            _transcript_event(output_text="Hi", output_finished=True),
            _transcript_event(input_text="How are", input_finished=False),
        ]
    )
    monkeypatch.setattr(gemini_live, "live_connect", lambda api_key, config: session)

    with TestClient(app) as client:
        with client.websocket_connect("/ws/voice") as ws:
            ws.receive_json()
            ws.receive_json()
            messages = [ws.receive_json() for _ in range(4)]

    assert messages == [
        {
            "type": "transcript",
            "speaker": "customer",
            "text": "hel",
            "partial": True,
        },
        {
            "type": "transcript",
            "speaker": "customer",
            "text": "hello",
            "partial": False,
        },
        {
            "type": "transcript",
            "speaker": "assistant",
            "text": "Hi",
            "partial": False,
        },
        {
            "type": "transcript",
            "speaker": "customer",
            "text": "How are",
            "partial": True,
        },
    ]


def test_transcript_fragments_preserve_speaker_order(monkeypatch):
    session = FakeSession(
        [
            _transcript_event(input_text="Acme", input_finished=False),
            _transcript_event(input_text="Acme Ltd", input_finished=True),
            _transcript_event(output_text="Thanks", output_finished=False),
            _transcript_event(output_text="Thanks for calling", output_finished=True),
        ]
    )
    monkeypatch.setattr(gemini_live, "live_connect", lambda api_key, config: session)

    with TestClient(app) as client:
        with client.websocket_connect("/ws/voice") as ws:
            ws.receive_json()
            ws.receive_json()
            messages = [ws.receive_json() for _ in range(4)]

    assert [(message["speaker"], message["text"], message["partial"]) for message in messages] == [
        ("customer", "Acme", True),
        ("customer", "Acme Ltd", False),
        ("assistant", "Thanks", True),
        ("assistant", "Thanks for calling", False),
    ]


def test_idle_nudge_fires_twice_without_transcript():
    class FakeNudgeSession:
        def __init__(self):
            self.sent = []

        async def send_realtime_input(self, **kwargs):
            self.sent.append(kwargs)

    async def scenario():
        session = FakeNudgeSession()
        stop = asyncio.Event()
        state = {"count": 0}
        for _ in range(3):
            task = asyncio.create_task(
                gemini_live._idle_nudge_loop(
                    session, stop, asyncio.Event(), state
                )
            )
            await task
        assert session.sent == [
            {"text": IDLE_NUDGE_TRIGGER},
            {"text": IDLE_NUDGE_TRIGGER},
        ]

    original = gemini_live.IDLE_NUDGE_SECONDS
    gemini_live.IDLE_NUDGE_SECONDS = 0.001
    try:
        asyncio.run(scenario())
    finally:
        gemini_live.IDLE_NUDGE_SECONDS = original


def test_idle_nudge_is_cancelled_by_speech_and_end():
    class FakeNudgeSession:
        async def send_realtime_input(self, **kwargs):
            raise AssertionError("nudge should have been cancelled")

    async def scenario():
        for stop_set, caller_set in ((False, True), (True, False)):
            stop = asyncio.Event()
            caller = asyncio.Event()
            if stop_set:
                stop.set()
            if caller_set:
                caller.set()
            task = asyncio.create_task(
                gemini_live._idle_nudge_loop(
                    FakeNudgeSession(), stop, caller, {"count": 0}
                )
            )
            await asyncio.sleep(0.01)
            await asyncio.gather(task, return_exceptions=True)

    original = gemini_live.IDLE_NUDGE_SECONDS
    gemini_live.IDLE_NUDGE_SECONDS = 0.001
    try:
        asyncio.run(scenario())
    finally:
        gemini_live.IDLE_NUDGE_SECONDS = original


def test_receive_loop_forwards_multiple_turns_and_stays_open(monkeypatch):
    hang = asyncio.Event()
    session = FakeSession(
        [],
        hang=hang,
        receive_batches=[
            [_audio_event(b"\x01\x00" * 8), _complete_event()],
            [_audio_event(b"\x02\x00" * 8), _complete_event()],
        ],
    )
    monkeypatch.setattr(gemini_live, "live_connect", lambda api_key, config: session)

    with TestClient(app) as client:
        with client.websocket_connect("/ws/voice") as ws:
            ws.receive_json()
            ws.receive_json()
            assert ws.receive_json() == {"type": "status", "value": "speaking"}
            assert ws.receive_bytes() == b"\x01\x00" * 8
            assert ws.receive_json() == {"type": "status", "value": "listening"}
            assert _receive_until(ws, "status") == {
                "type": "status",
                "value": "speaking",
            }
            assert ws.receive_bytes() == b"\x02\x00" * 8
            assert _receive_until(ws, "status") == {
                "type": "status",
                "value": "listening",
            }
            assert session.closed is False
            ws.send_json({"type": "end"})

    assert session.closed is True


def test_kickoff_is_sent_once_and_not_as_customer_transcript(monkeypatch):
    session = FakeSession(
        [
            _transcript_event(output_text="Hello, what does your business do?", output_finished=True),
        ]
    )
    monkeypatch.setattr(gemini_live, "live_connect", lambda api_key, config: session)

    with TestClient(app) as client:
        with client.websocket_connect("/ws/voice") as ws:
            ws.receive_json()
            ws.receive_json()
            transcript = ws.receive_json()

    assert session.sent == [{"text": GREETING_TRIGGER}]
    assert transcript["speaker"] == "assistant"
    assert transcript["text"] != GREETING_TRIGGER

def test_duration_message_uses_server_clock(monkeypatch):
    sent = []

    class FakeWebSocket:
        async def send_json(self, message):
            sent.append(message)

    times = iter([100.0, 103.9])
    monkeypatch.setattr(
        gemini_live, "time", SimpleNamespace(monotonic=lambda: next(times))
    )

    asyncio.run(
        gemini_live._send_duration(
            FakeWebSocket(), started_at=next(times), stop=asyncio.Event(), final=True
        )
    )

    assert sent == [{"type": "duration", "seconds": 3, "final": True}]


def test_cleanup_releases_gate_after_disconnect(monkeypatch):
    hang = asyncio.Event()
    session = FakeSession([], hang=hang)
    monkeypatch.setattr(gemini_live, "live_connect", lambda api_key, config: session)

    with TestClient(app) as client:
        with client.websocket_connect("/ws/voice") as ws:
            ws.receive_json()
            ws.receive_json()
        with client.websocket_connect("/ws/voice") as ws2:
            assert ws2.receive_json()["value"] == "connecting"

    assert session.closed is True


def test_duplicate_session_rejected(monkeypatch):
    hang = asyncio.Event()
    session = FakeSession([], hang=hang)
    monkeypatch.setattr(gemini_live, "live_connect", lambda api_key, config: session)

    with TestClient(app) as client:
        with client.websocket_connect("/ws/voice") as first:
            first.receive_json()
            first.receive_json()
            with client.websocket_connect("/ws/voice") as second:
                payload = second.receive_json()
                assert payload == {
                    "type": "error",
                    "message": "A call is already in progress.",
                }