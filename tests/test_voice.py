import asyncio
import time
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.routes import voice as voice_route
from app.services import gemini_live


class FakeSession:
    def __init__(self, events, hang: asyncio.Event | None = None):
        self.events = events
        self.hang = hang
        self.sent = []
        self.closed = False

    async def send_realtime_input(self, **kwargs):
        self.sent.append(kwargs)

    async def receive(self):
        for event in self.events:
            yield event
        if self.hang is not None:
            await self.hang.wait()

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
    assert session.sent[0].get("audio") is not None


def test_interrupt_is_forwarded(monkeypatch):
    session = FakeSession([_interrupt_event()])
    monkeypatch.setattr(gemini_live, "live_connect", lambda api_key, config: session)

    with TestClient(app) as client:
        with client.websocket_connect("/ws/voice") as ws:
            ws.receive_json()
            ws.receive_json()
            assert ws.receive_json() == {"type": "interrupted"}


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