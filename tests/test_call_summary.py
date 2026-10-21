import json
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


def test_summary_schema_has_no_additional_properties():
    def assert_no_additional_properties(value):
        if isinstance(value, dict):
            assert "additionalProperties" not in value
            for nested_value in value.values():
                assert_no_additional_properties(nested_value)
        elif isinstance(value, list):
            for nested_value in value:
                assert_no_additional_properties(nested_value)

    assert_no_additional_properties(call_summary.CallSummary.model_json_schema())


def test_empty_transcript_does_not_call_model(monkeypatch):
    def fail_if_called(*args, **kwargs):
        raise AssertionError("model should not be called")

    monkeypatch.setattr(call_summary.genai, "Client", fail_if_called)
    summary = call_summary.generate_summary([])
    assert summary.status is CallStatus.NO_RESPONSE


def test_summary_client_uses_fast_generation_settings(monkeypatch):
    captured = {}

    class FakeModels:
        def generate_content(self, **kwargs):
            captured["config"] = kwargs["config"]
            return SimpleNamespace(text=json.dumps(_valid_summary()))

    class FakeClient:
        def __init__(self, **kwargs):
            captured.update(kwargs)
            self.models = FakeModels()

        def close(self):
            pass

    monkeypatch.setattr(call_summary.settings, "GEMINI_API_KEY", "test-key")
    monkeypatch.setattr(call_summary.genai, "Client", FakeClient)

    call_summary.generate_summary([{"speaker": "customer", "text": "Hello"}])

    assert captured["http_options"].timeout == 90000
    assert captured["config"].max_output_tokens == 1500
    assert captured["config"].temperature == 0.2
    assert captured["config"].thinking_config.thinking_level.value == "MINIMAL"


def test_summary_retries_server_error_once(monkeypatch):
    attempts = 0

    class FakeModels:
        def generate_content(self, **kwargs):
            nonlocal attempts
            attempts += 1
            if attempts == 1:
                raise call_summary.errors.ServerError(
                    504, {"error": {"message": "deadline exceeded"}}
                )
            return SimpleNamespace(text=json.dumps(_valid_summary()))

    class FakeClient:
        def __init__(self, **kwargs):
            self.models = FakeModels()

        def close(self):
            pass

    monkeypatch.setattr(call_summary.settings, "GEMINI_API_KEY", "test-key")
    monkeypatch.setattr(call_summary.genai, "Client", FakeClient)

    summary = call_summary.generate_summary(
        [{"speaker": "customer", "text": "Hello"}]
    )

    assert summary.status is CallStatus.INTERESTED
    assert attempts == 2


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


def test_status_schema_rejects_unknown_value():
    with pytest.raises(ValidationError):
        call_summary.CallSummary.model_validate(
            {**_valid_summary(), "status": "Maybe"}
        )
