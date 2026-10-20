import json
import logging
from typing import Any

from google import genai
from google.genai import types
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from app.config import settings
from app.models.call import CallStatus

SUMMARY_MODEL = "gemini-2.5-flash"
logger = logging.getLogger(__name__)


class CallSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    business_use_case: str = ""
    needs: list[str] = Field(default_factory=list)
    modules_discussed: list[str] = Field(default_factory=list)
    questions: list[str] = Field(default_factory=list)
    objections: list[str] = Field(default_factory=list)
    interest: str = ""
    interest_evidence: list[str] = Field(default_factory=list)
    next_steps: list[str] = Field(default_factory=list)
    unknowns: list[str] = Field(default_factory=list)
    status: CallStatus


class SummaryGenerationError(RuntimeError):
    pass


def has_customer_speech(transcript: list[dict[str, Any]]) -> bool:
    return any(
        entry.get("speaker") == "customer"
        and isinstance(entry.get("text"), str)
        and entry["text"].strip()
        for entry in transcript
    )


def parse_summary_response(response: Any) -> CallSummary:
    parsed = getattr(response, "parsed", None)
    if parsed is not None:
        try:
            return CallSummary.model_validate(parsed)
        except ValidationError as exc:
            raise SummaryGenerationError("Gemini returned an invalid summary.") from exc

    text = getattr(response, "text", None)
    if not isinstance(text, str) or not text.strip():
        raise SummaryGenerationError("Gemini returned an empty summary.")
    try:
        payload = json.loads(text)
        return CallSummary.model_validate(payload)
    except (json.JSONDecodeError, ValidationError) as exc:
        raise SummaryGenerationError("Gemini returned malformed summary JSON.") from exc


def generate_summary(transcript: list[dict[str, Any]]) -> CallSummary:
    if not has_customer_speech(transcript):
        return CallSummary(
            business_use_case="",
            unknowns=["No customer speech was captured."],
            status=CallStatus.NO_RESPONSE,
        )
    if not settings.GEMINI_API_KEY:
        raise SummaryGenerationError("GEMINI_API_KEY is not configured.")

    conversation = "\n".join(
        f"{entry.get('speaker', 'unknown')}: {entry.get('text', '')}"
        for entry in transcript
        if entry.get("text")
    )
    prompt = (
        "Summarize this sales conversation. Use only information explicitly present; "
        "put missing information in unknowns and do not invent Surefy facts. "
        "Choose the most conservative applicable status.\n\n"
        f"Transcript:\n{conversation}"
    )
    try:
        client = genai.Client(api_key=settings.GEMINI_API_KEY)
        response = client.models.generate_content(
            model=SUMMARY_MODEL,
            contents=prompt,
            config=types.GenerateContentConfig(
                response_mime_type="application/json",
                response_schema=CallSummary.model_json_schema(),
            ),
        )
        return parse_summary_response(response)
    except SummaryGenerationError:
        raise
    except Exception as exc:
        logger.exception("Call summary generation failed.")
        raise SummaryGenerationError("Call summary generation failed.") from exc
    finally:
        close = locals().get("client")
        if close is not None and callable(getattr(close, "close", None)):
            close.close()


def summary_json(summary: CallSummary) -> str:
    return summary.model_dump_json()
