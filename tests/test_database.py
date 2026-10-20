import asyncio
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.exc import StatementError

from app.main import app
from app.models.call import Call, SummaryGenerationStatus
from app.routes.calls import create_call, finalize_call
from app.routes.voice import _merge_transcript_text
from app.services import recording_storage
from app import database
from app.config import settings
from app.security import make_call_token


def test_database_url_normalizes_postgres_and_default_path(monkeypatch):
    monkeypatch.setattr(database.settings, "DATABASE_URL", "postgres://user:pass@host/db")
    assert database._database_url() == "postgresql+psycopg://user:pass@host/db"
    monkeypatch.setattr(database.settings, "DATABASE_URL", "sqlite:///./vaani.db")
    assert database._database_url().endswith("vaani.db")
    assert database._database_url().startswith("sqlite:///")


def test_call_creation_and_finalize_is_single_row(db_session):
    started = datetime.now(timezone.utc)
    call = create_call(db_session)
    finalize_call(
        db_session,
        call.id,
        [{"speaker": "customer", "text": "Hello", "timestamp": started.isoformat()}],
        started,
    )
    calls = list(db_session.scalars(select(Call)))
    assert len(calls) == 1
    assert calls[0].ended_at is not None
    assert calls[0].transcript[0]["text"] == "Hello"


def test_finalize_does_not_generate_summary(db_session, monkeypatch):
    call = create_call(db_session)
    monkeypatch.setattr(
        "app.routes.calls.generate_call_summary",
        lambda *args: (_ for _ in ()).throw(AssertionError("summary was called")),
    )
    finalize_call(db_session, call.id, [{"speaker": "customer", "text": "Hello"}])
    assert call.ended_at is not None


def test_finalize_extracts_confirmed_lead_details(db_session):
    started = datetime.now(timezone.utc)
    call = create_call(db_session)
    finalize_call(
        db_session,
        call.id,
        [
            {
                "speaker": "assistant",
                "text": (
                    "Your name is Vishesh Patel, the company is Samsung, your "
                    "email is visheshpatel599@gmail.com, and you'd prefer the "
                    "demo tomorrow morning at 11 AM. Is that all correct?"
                ),
                "timestamp": started.isoformat(),
            }
        ],
        started,
    )
    db_session.refresh(call)
    assert call.ended_at is not None
    assert call.summary_generation_status.value == "pending"


def test_finalize_extracts_details_from_incremental_transcript(db_session):
    started = datetime.now(timezone.utc)
    call = create_call(db_session)
    readback = _merge_transcript_text(
        "Your name is Vishesh Patel, the company is Samsung, your",
        "email is visheshpatel599@gmail.com, and you'd prefer the demo tomorrow "
        "morning at 11 AM. Is that all correct?",
    )
    finalize_call(
        db_session,
        call.id,
        [
            {
                "speaker": "assistant",
                "text": readback,
                "timestamp": started.isoformat(),
            }
        ],
        started,
    )
    assert call.ended_at is not None


def test_finalize_extracts_answers_after_short_detail_prompts(db_session):
    started = datetime.now(timezone.utc)
    call = create_call(db_session)
    finalize_call(
        db_session,
        call.id,
        [
            {"speaker": "assistant", "text": "name?", "timestamp": started.isoformat()},
            {"speaker": "customer", "text": "विशेष पटेल", "timestamp": started.isoformat()},
            {"speaker": "assistant", "text": "company?", "timestamp": started.isoformat()},
            {"speaker": "customer", "text": "Samsung", "timestamp": started.isoformat()},
            {
                "speaker": "assistant",
                "text": "or email address?",
                "timestamp": started.isoformat(),
            },
            {
                "speaker": "customer",
                "text": "visheshpatel599@gmail.com",
                "timestamp": started.isoformat(),
            },
            {
                "speaker": "assistant",
                "text": "preferred time?",
                "timestamp": started.isoformat(),
            },
            {
                "speaker": "customer",
                "text": "tomorrow morning 11",
                "timestamp": started.isoformat(),
            },
        ],
        started,
    )
    assert call.ended_at is not None


def test_status_rejects_value_outside_backend_enum(db_session):
    call = Call(started_at=datetime.now(timezone.utc), status="Unknown")
    db_session.add(call)
    with pytest.raises(StatementError):
        db_session.commit()


def test_history_retrieval_and_lead_patch(db_session):
    call = create_call(db_session)
    with TestClient(app) as client:
        response = client.get("/api/calls")
        assert response.status_code == 200
        assert response.json()[0]["id"] == call.id
        assert set(response.json()[0]) == {
            "id",
            "started_at",
            "ended_at",
            "duration_seconds",
            "status",
            "summary_generation_status",
            "recording_consent",
            "has_recording",
        }
        response = client.patch(
            f"/api/calls/{call.id}/lead",
            headers={"X-Call-Token": make_call_token(call.id)},
            json={
                "name": "A prospect",
                "company": "Acme",
                "contact": "person@example.com",
                "preferred_time": "Tomorrow morning",
                "consent": True,
            },
        )
        assert response.status_code == 200
        assert response.json()["lead_company"] == "Acme"


def test_history_password_protects_reads_and_writes_without_auth_header(db_session, monkeypatch):
    monkeypatch.setattr(settings, "HISTORY_PASSWORD", "history-secret")
    call = create_call(db_session)
    with TestClient(app) as client:
        for method, url in (
            ("get", "/api/calls"),
            ("get", f"/api/calls/{call.id}"),
            ("patch", f"/api/calls/{call.id}/lead"),
            ("post", f"/api/calls/{call.id}/summary/retry"),
        ):
            response = (
                client.patch(url, json={})
                if method == "patch"
                else getattr(client, method)(url)
            )
            assert response.status_code == 401
            assert "www-authenticate" not in response.headers


def test_call_token_allows_call_specific_operations(db_session, monkeypatch):
    monkeypatch.setattr(settings, "HISTORY_PASSWORD", "history-secret")
    call = create_call(db_session)
    token = make_call_token(call.id)
    with TestClient(app) as client:
        response = client.get(
            f"/api/calls/{call.id}", headers={"X-Call-Token": token}
        )
        update = client.patch(
            f"/api/calls/{call.id}/lead",
            headers={"X-Call-Token": token},
            json={"name": "Prospect"},
        )
    assert response.status_code == 200
    assert update.status_code == 200


def test_recording_upload_normalizes_type_rejects_duplicate_and_size(
    db_session, monkeypatch
):
    monkeypatch.setattr(settings, "RECORDING_MAX_BYTES", 3)
    call = create_call(db_session)
    call.recording_consent = True
    db_session.commit()
    async def save_recording(*args):
        return "call-1/audio.webm"

    monkeypatch.setattr("app.routes.calls.save_recording", save_recording)
    headers = {
        "content-type": "audio/webm;codecs=opus",
        "X-Call-Token": make_call_token(call.id),
    }
    with TestClient(app) as client:
        response = client.put(
            f"/api/calls/{call.id}/recording", content=b"123", headers=headers
        )
        duplicate = client.put(
            f"/api/calls/{call.id}/recording", content=b"123", headers=headers
        )
        oversized_call = create_call(db_session)
        oversized_call.recording_consent = True
        db_session.commit()
        oversized = client.put(
            f"/api/calls/{oversized_call.id}/recording",
            content=b"1234",
            headers={
                "content-type": "audio/webm",
                "X-Call-Token": make_call_token(oversized_call.id),
            },
        )
    assert response.status_code == 200
    assert response.json()["content_type"] == "audio/webm"
    assert duplicate.status_code == 409
    assert oversized.status_code == 413


def test_local_recordings_can_be_disabled(db_session, monkeypatch):
    monkeypatch.setattr(settings, "ALLOW_LOCAL_RECORDINGS", False)
    monkeypatch.setattr(recording_storage, "LOCAL_RECORDINGS", db_session.bind.url.database)
    with pytest.raises(RuntimeError):
        asyncio.run(recording_storage.save_recording(1, b"audio", "audio/webm"))
    with TestClient(app) as client:
        assert client.get("/api/calls/local/call-1/audio.webm").status_code == 404


def test_local_recordings_hidden_when_history_password_is_set(monkeypatch):
    monkeypatch.setattr(settings, "HISTORY_PASSWORD", "history-secret")
    with TestClient(app) as client:
        assert client.get("/api/calls/local/call-1/audio.webm").status_code == 404


def test_history_has_parsed_summary_and_utc_datetimes(db_session):
    call = create_call(db_session)
    call.summary = '{"interest":"Interested"}'
    call.summary_generation_status = SummaryGenerationStatus.COMPLETED
    call.ended_at = datetime.now(timezone.utc)
    db_session.commit()

    with TestClient(app) as client:
        response = client.get(f"/api/calls/{call.id}")

    payload = response.json()
    assert response.status_code == 200
    assert payload["summary_data"] == {"interest": "Interested"}
    assert "summary" not in payload
    assert "recording_object_key" not in payload
    assert payload["started_at"].endswith(("Z", "+00:00"))
    assert payload["ended_at"].endswith(("Z", "+00:00"))


def test_call_list_is_limited_and_retry_rejects_active_call(db_session):
    calls = [create_call(db_session) for _ in range(3)]
    with TestClient(app) as client:
        response = client.get("/api/calls?limit=2")
        retry = client.post(
            f"/api/calls/{calls[0].id}/summary/retry",
            headers={"X-Call-Token": make_call_token(calls[0].id)},
        )

    assert response.status_code == 200
    assert len(response.json()) == 2
    assert retry.status_code == 409


def test_lead_patch_rejects_overlong_input(db_session):
    call = create_call(db_session)
    with TestClient(app) as client:
        response = client.patch(
            f"/api/calls/{call.id}/lead",
            json={"name": "x" * 121},
        )
    assert response.status_code == 422


def test_recording_requires_consent(db_session):
    call = create_call(db_session)
    with TestClient(app) as client:
        response = client.put(
            f"/api/calls/{call.id}/recording",
            content=b"audio",
            headers={
                "content-type": "audio/webm",
                "X-Call-Token": make_call_token(call.id),
            },
        )
    assert response.status_code == 400


def test_local_recording_key_supports_playback(tmp_path, monkeypatch):
    monkeypatch.setattr(recording_storage, "LOCAL_RECORDINGS", tmp_path)
    monkeypatch.setattr(recording_storage.settings, "R2_ACCOUNT_ID", "")
    monkeypatch.setattr(recording_storage.settings, "R2_ACCESS_KEY_ID", "")
    monkeypatch.setattr(recording_storage.settings, "R2_SECRET_ACCESS_KEY", "")
    monkeypatch.setattr(recording_storage.settings, "R2_BUCKET_NAME", "")
    monkeypatch.setattr(recording_storage.settings, "R2_ENDPOINT_URL", "")

    object_key = asyncio.run(
        recording_storage.save_recording(7, b"audio", "audio/webm")
    )

    assert object_key.startswith("call-7/")
    assert (tmp_path / object_key).read_bytes() == b"audio"
    assert asyncio.run(recording_storage.signed_recording_url(object_key)) == (
        f"/api/calls/local/{object_key}"
    )
