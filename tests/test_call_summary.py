from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.main import app
from app.models.call import CallStatus, SummaryGenerationStatus
from app.routes.calls import create_call, finalize_call, generate_call_summary
from app.security import make_call_token
from app.services import call_summary


def _valid_summary() -> dict:
    return {
        "business_use_case": "Sales outreach",
        "needs": ["More qualified leads"],
        "modules_discussed": ["Reachout"],
        "questions": ["How does setup work?"],
        "objections": ["Needs internal approval"],
        "interest": "Interested",
        "interest_evidence": ["Asked about setup"],
        "next_steps": ["Follow up next week"],
        "unknowns": [],
        "status": "Interested",
    }


def test_summary_parser_validates_json_and_status():
    import json

    summary = call_summary.parse_summary_response(
        SimpleNamespace(text=json.dumps(_valid_summary()))
    )
    assert summary.status is CallStatus.INTERESTED
    with pytest.raises(call_summary.SummaryGenerationError):
        call_summary.parse_summary_response(SimpleNamespace(text='{"status":"invalid"}'))


def test_empty_transcript_does_not_call_model(monkeypatch):
    def fail_if_called(*args, **kwargs):
        raise AssertionError("model should not be called")

    monkeypatch.setattr(call_summary.genai, "Client", fail_if_called)
    summary = call_summary.generate_summary([])
    assert summary.status is CallStatus.NO_RESPONSE


def test_generation_failure_marks_record_failed(db_session, monkeypatch):
    call = create_call(db_session)
    call.transcript = [{"speaker": "customer", "text": "Hello"}]
    db_session.commit()
    monkeypatch.setattr(
        "app.routes.calls.generate_summary",
        lambda transcript: (_ for _ in ()).throw(
            call_summary.SummaryGenerationError("failed")
        ),
    )

    generate_call_summary(db_session, call)

    assert call.summary_generation_status is SummaryGenerationStatus.FAILED
    assert call.status is None


def test_retry_updates_same_record(db_session, monkeypatch):
    call = create_call(db_session)
    call.transcript = [{"speaker": "customer", "text": "We need Reachout"}]
    finalize_call(db_session, call.id, call.transcript)
    call.summary_generation_status = SummaryGenerationStatus.FAILED
    db_session.commit()
    monkeypatch.setattr(
        "app.routes.calls.generate_summary",
        lambda transcript: call_summary.CallSummary.model_validate(_valid_summary()),
    )

    with TestClient(app) as client:
        response = client.post(
            f"/api/calls/{call.id}/summary/retry",
            headers={"X-Call-Token": make_call_token(call.id)},
        )
    assert response.status_code == 200
    assert response.json()["id"] == call.id
    assert response.json()["status"] == "Interested"
    assert response.json()["summary_generation_status"] == "completed"


def test_summary_prefills_empty_lead_columns_without_consent(db_session, monkeypatch):
    call = create_call(db_session)
    call.transcript = [{"speaker": "customer", "text": "My name is Ada"}]
    call.lead_contact_consent = None
    db_session.commit()
    summary_data = {
        **_valid_summary(),
        "lead_name": "Ada",
        "lead_company": "Acme",
        "lead_contact": "ada@example.com",
        "lead_preferred_time": "Tomorrow morning",
    }
    monkeypatch.setattr(
        "app.routes.calls.generate_summary",
        lambda transcript: call_summary.CallSummary.model_validate(summary_data),
    )

    generate_call_summary(db_session, call)

    assert call.lead_name == "Ada"
    assert call.lead_company == "Acme"
    assert call.lead_contact == "ada@example.com"
    assert call.lead_preferred_time == "Tomorrow morning"
    assert call.lead_contact_consent is None


def test_status_schema_rejects_unknown_value():
    with pytest.raises(ValidationError):
        call_summary.CallSummary.model_validate(
            {**_valid_summary(), "status": "Maybe"}
        )
