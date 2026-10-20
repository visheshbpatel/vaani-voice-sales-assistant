import asyncio
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.exc import StatementError
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base, get_db
from app.main import app
from app.models.call import Call
from app.routes.calls import create_call, finalize_call
from app.routes.voice import _merge_transcript_text
from app.services import recording_storage


@pytest.fixture
def db_session():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine, expire_on_commit=False)()
    try:
        yield session
    finally:
        session.close()


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
    assert call.lead_name == "Vishesh Patel"
    assert call.lead_company == "Samsung"
    assert call.lead_contact == "visheshpatel599@gmail.com"
    assert call.lead_preferred_time == "tomorrow morning at 11 AM"


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
    assert call.lead_name == "Vishesh Patel"


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
    assert call.lead_name == "विशेष पटेल"
    assert call.lead_company == "Samsung"
    assert call.lead_contact == "visheshpatel599@gmail.com"
    assert call.lead_preferred_time == "tomorrow morning 11"


def test_status_rejects_value_outside_backend_enum(db_session):
    call = Call(started_at=datetime.now(timezone.utc), status="Unknown")
    db_session.add(call)
    with pytest.raises(StatementError):
        db_session.commit()


def test_history_retrieval_and_lead_patch(db_session):
    call = create_call(db_session)

    def override_db():
        yield db_session

    app.dependency_overrides[get_db] = override_db
    try:
        with TestClient(app) as client:
            response = client.get("/api/calls")
            assert response.status_code == 200
            assert response.json()[0]["id"] == call.id
            response = client.patch(
                f"/api/calls/{call.id}/lead",
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
    finally:
        app.dependency_overrides.clear()


def test_lead_patch_rejects_overlong_input(db_session):
    call = create_call(db_session)

    def override_db():
        yield db_session

    app.dependency_overrides[get_db] = override_db
    try:
        with TestClient(app) as client:
            response = client.patch(
                f"/api/calls/{call.id}/lead",
                json={"name": "x" * 121},
            )
        assert response.status_code == 422
    finally:
        app.dependency_overrides.clear()


def test_recording_requires_consent(db_session):
    call = create_call(db_session)

    def override_db():
        yield db_session

    app.dependency_overrides[get_db] = override_db
    try:
        with TestClient(app) as client:
            response = client.put(
                f"/api/calls/{call.id}/recording",
                content=b"audio",
                headers={"content-type": "audio/webm"},
            )
        assert response.status_code == 400
    finally:
        app.dependency_overrides.clear()


def test_local_recording_key_supports_playback(tmp_path, monkeypatch):
    monkeypatch.setattr(recording_storage, "LOCAL_RECORDINGS", tmp_path)
    monkeypatch.setattr(recording_storage.settings, "SUPABASE_URL", "")
    monkeypatch.setattr(recording_storage.settings, "SUPABASE_SERVICE_ROLE_KEY", "")

    object_key = asyncio.run(
        recording_storage.save_recording(7, b"audio", "audio/webm")
    )

    assert object_key.startswith("call-7/")
    assert (tmp_path / object_key).read_bytes() == b"audio"
    assert asyncio.run(recording_storage.signed_recording_url(object_key)) == (
        f"/api/calls/local/{object_key}"
    )
