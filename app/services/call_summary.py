import json
import logging
import random
import time
from typing import Any

import httpx
from google import genai
from google.genai import errors, types
from pydantic import BaseModel, Field, ValidationError

from app.config import settings
from app.models.call import CallStatus

logger = logging.getLogger(__name__)
SUMMARY_MAX_ATTEMPTS = 3
SUMMARY_RETRY_BASE_DELAY_SECONDS = 0.5


class CallSummary(BaseModel):
    business_use_case: str = ""
    needs: list[str] = Field(default_factory=list)
    modules_discussed: list[str] = Field(default_factory=list)
    questions: list[str] = Field(default_factory=list)
    objections: list[str] = Field(default_factory=list)
    interest: str = ""
    interest_evidence: list[str] = Field(default_factory=list)
    next_steps: list[str] = Field(default_factory=list)
    unknowns: list[str] = Field(default_factory=list)
    lead_name: str = ""
    lead_company: str = ""
    lead_contact: str = ""
    lead_preferred_time: str = ""
    status: CallStatus


def _conservative_status(
    summary: CallSummary, transcript: list[dict[str, Any]]
) -> CallSummary:
    customer_text = " ".join(
        str(entry.get("text", "")).lower()
        for entry in transcript
        if entry.get("speaker") == "customer"
    )
    evidence = {
        CallStatus.CONVERTED: (
            "converted", "signed up", "sign up", "purchase", "buy", "go ahead",
            "proceed",
        ),
        CallStatus.DEMO_REQUESTED: (
            "request a demo", "requested a demo", "schedule a demo", "book a demo",
            "see a demo", "demo",
        ),
        CallStatus.NOT_INTERESTED: (
            "not interested", "no thank", "don't need", "do not need", "not for us",
            "decline",
        ),
        CallStatus.INTERESTED: (
            "interested", "sounds good", "like to learn", "tell me more",
        ),
    }
    if summary.status in evidence and not any(
        phrase in customer_text for phrase in evidence[summary.status]
    ):
        summary.status = CallStatus.FOLLOW_UP_REQUIRED
    return summary


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


def _is_retryable_generation_error(exc: Exception) -> bool:
    if isinstance(exc, (TimeoutError, httpx.TimeoutException)):
        return True
    if not isinstance(exc, errors.APIError):
        return False
    return exc.code in {408, 429} or 500 <= exc.code <= 504


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
        "Use these status definitions: Converted means there is real evidence of a completed "
        "conversion; Interested means the customer expressed interest without requesting a demo; "
        "Follow-up Required means more contact is needed; Not Interested means the customer "
        "declined; Demo Requested means the customer explicitly requested a demo; No Response "
        "means no customer speech was captured. choose the most conservative status. "
        "Include lead_name, lead_company, lead_contact, and lead_preferred_time only when the "
        "customer explicitly said them; otherwise use an empty string. Read the transcript "
        "carefully for the customer's name, company, phone number or email, and preferred "
        "time for follow-up, including spoken digits and spelled-out email addresses. "
        "Normalize phone numbers to digits only, and normalize emails by converting spoken "
        "\"at\" and \"dot\" to @ and .; still use only what the customer explicitly said.\n\n"
        f"Transcript:\n{conversation}"
    )
    try:
        client = genai.Client(
            api_key=settings.GEMINI_API_KEY,
            # google-genai expects this timeout in milliseconds.
            http_options=types.HttpOptions(timeout=90000),
        )
        config = types.GenerateContentConfig(
            response_mime_type="application/json",
            response_schema=CallSummary,
            max_output_tokens=1500,
            temperature=0.2,
        )
        for attempt in range(SUMMARY_MAX_ATTEMPTS):
            try:
                response = client.models.generate_content(
                    model=settings.SUMMARY_MODEL,
                    contents=prompt,
                    config=config,
                )
                return _conservative_status(
                    parse_summary_response(response), transcript
                )
            except Exception as exc:
                if (
                    attempt + 1 < SUMMARY_MAX_ATTEMPTS
                    and _is_retryable_generation_error(exc)
                ):
                    delay = SUMMARY_RETRY_BASE_DELAY_SECONDS * (2**attempt)
                    delay += random.uniform(0, delay / 2)
                    logger.warning(
                        "Retrying call summary generation after a transient error "
                        "(attempt %d/%d).",
                        attempt + 1,
                        SUMMARY_MAX_ATTEMPTS,
                    )
                    time.sleep(delay)
                    continue
                raise
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
