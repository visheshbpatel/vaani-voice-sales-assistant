import logging
import re
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse
from pathlib import Path
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.call import Call, CallStatus, SummaryGenerationStatus
from app.schemas.call import CallHistory, LeadUpdate
from app.services.recording_storage import save_recording, signed_recording_url
from app.config import settings
from app.services.call_summary import (
    SummaryGenerationError,
    generate_summary,
    summary_json,
)

router = APIRouter(prefix="/api/calls", tags=["calls"])
logger = logging.getLogger(__name__)


def _extract_lead_details(
    transcript: list[dict[str, str]],
) -> dict[str, str | None]:
    assistant_text = " ".join(
        entry["text"]
        for entry in transcript
        if entry.get("speaker") == "assistant" and entry.get("text")
    )
    name = re.search(
        r"(?:your|the) name is\s+(.+?)(?=,\s*(?:the )?company is|,\s*company is)",
        assistant_text,
        re.IGNORECASE,
    )
    company = re.search(
        r"(?:the )?company is\s+(.+?)(?=,\s*(?:your )?(?:email|phone)|,\s*and\s+(?:your )?(?:email|phone))",
        assistant_text,
        re.IGNORECASE,
    )
    contact = re.search(r"[^\s,;]+@[^\s,;]+\.[^\s,;]+", assistant_text)
    preferred = re.search(
        r"(?:you.?d prefer|preferred time is|prefer(?:red)? time(?: would be)? is?)"
        r"\s+(.+?)(?=\.\s|,\s*is that|\?|$)",
        assistant_text,
        re.IGNORECASE,
    )
    details: dict[str, str | None] = {
        "name": name.group(1).strip() if name else None,
        "company": company.group(1).strip() if company else None,
        "contact": contact.group(0) if contact else None,
        "preferred_time": (
            preferred.group(1).replace("the demo ", "", 1).strip(" .")
            if preferred
            else None
        ),
    }
    expected: str | None = None
    for entry in transcript:
        text = (entry.get("text") or "").strip()
        if not text:
            continue
        if entry.get("speaker") == "assistant":
            lowered = text.lower()
            if "full name" in lowered or lowered.endswith("name?"):
                expected = "name"
            elif "company" in lowered:
                expected = "company"
            elif "phone" in lowered or "email" in lowered:
                expected = "contact"
            elif "preferred time" in lowered or "best time" in lowered:
                expected = "preferred_time"
            elif (
                details["contact"]
                and "demo" in lowered
                and lowered.endswith("?")
            ):
                expected = "preferred_time"
            continue
        if entry.get("speaker") != "customer" or expected is None:
            continue
        answer = text.strip(" .!?")
        if expected == "contact":
            match = re.search(r"[^\s,;]+@[^\s,;]+\.[^\s,;]+", answer)
            if not details[expected]:
                details[expected] = match.group(0) if match else answer
        elif not details[expected]:
            details[expected] = answer
        expected = None
    return details


@router.get("", response_model=list[CallHistory])
def list_calls(db: Session = Depends(get_db)) -> list[Call]:
    try:
        return list(db.scalars(select(Call).order_by(Call.created_at.desc())).all())
    except SQLAlchemyError:
        logger.exception("Could not retrieve call history.")
        raise HTTPException(status_code=503, detail="Call history is temporarily unavailable.")


@router.get("/{call_id}", response_model=CallHistory)
def get_call(call_id: int, db: Session = Depends(get_db)) -> Call:
    try:
        call = db.get(Call, call_id)
    except SQLAlchemyError:
        logger.exception("Could not retrieve call record.")
        raise HTTPException(status_code=503, detail="Call history is temporarily unavailable.")
    if call is None:
        raise HTTPException(status_code=404, detail="Call record not found.")
    return call


@router.patch("/{call_id}/lead", response_model=CallHistory)
def update_lead(call_id: int, update: LeadUpdate, db: Session = Depends(get_db)) -> Call:
    call = db.get(Call, call_id)
    if call is None:
        raise HTTPException(status_code=404, detail="Call record not found.")
    call.lead_name = update.name
    call.lead_company = update.company
    call.lead_contact = update.contact
    call.lead_preferred_time = update.preferred_time
    call.lead_contact_consent = update.consent
    try:
        db.commit()
        db.refresh(call)
    except SQLAlchemyError:
        db.rollback()
        logger.exception("Could not update call lead details.")
        raise HTTPException(status_code=503, detail="Call history is temporarily unavailable.")
    return call


def generate_call_summary(db: Session, call: Call) -> Call:
    try:
        summary = generate_summary(call.transcript)
    except SummaryGenerationError:
        call.summary_generation_status = SummaryGenerationStatus.FAILED
        call.status = CallStatus.FOLLOW_UP_REQUIRED
        call.summary = None
    else:
        call.summary = summary_json(summary)
        call.status = summary.status
        call.customer_requirements = "\n".join(summary.needs) or None
        call.objections = "\n".join(summary.objections) or None
        call.next_steps = "\n".join(summary.next_steps) or None
        call.summary_generation_status = SummaryGenerationStatus.COMPLETED
    db.commit()
    db.refresh(call)
    return call


@router.post("/{call_id}/summary/retry", response_model=CallHistory)
def retry_summary(call_id: int, db: Session = Depends(get_db)) -> Call:
    call = db.get(Call, call_id)
    if call is None:
        raise HTTPException(status_code=404, detail="Call record not found.")
    if call.summary_generation_status == SummaryGenerationStatus.COMPLETED:
        return call
    try:
        return generate_call_summary(db, call)
    except SQLAlchemyError:
        db.rollback()
        logger.exception("Could not save regenerated call summary.")
        raise HTTPException(status_code=503, detail="Call summary could not be saved.")


@router.put("/{call_id}/recording")
async def upload_recording(call_id: int, request: Request, db: Session = Depends(get_db)):
    call = db.get(Call, call_id)
    if call is None:
        raise HTTPException(status_code=404, detail="Call record not found.")
    if not call.recording_consent:
        raise HTTPException(status_code=400, detail="Recording consent was not provided.")
    content_type = request.headers.get("content-type", "")
    if content_type not in {"audio/webm", "audio/ogg"}:
        raise HTTPException(status_code=415, detail="Unsupported recording format.")
    content = await request.body()
    if not content or len(content) > settings.RECORDING_MAX_BYTES:
        raise HTTPException(status_code=413, detail="Recording is empty or too large.")
    try:
        object_key = await save_recording(call_id, content, content_type)
        call.recording_object_key = object_key
        call.recording_content_type = content_type
        db.commit()
    except (SQLAlchemyError, RuntimeError):
        db.rollback()
        logger.exception("Could not save call recording.")
        raise HTTPException(status_code=503, detail="Recording could not be saved.")
    return {"saved": True, "content_type": content_type}


@router.get("/local/{object_key:path}")
def get_local_recording(object_key: str):
    root = Path("recordings").resolve()
    path = (root / object_key).resolve()
    if root not in path.parents or not path.is_file():
        raise HTTPException(status_code=404, detail="Recording is unavailable.")
    return FileResponse(path)


@router.get("/{call_id}/recording")
async def get_recording_url(call_id: int, db: Session = Depends(get_db)):
    call = db.get(Call, call_id)
    if call is None:
        raise HTTPException(status_code=404, detail="Call record not found.")
    if not call.recording_object_key:
        raise HTTPException(status_code=404, detail="Recording is unavailable.")
    try:
        return {"url": await signed_recording_url(call.recording_object_key)}
    except RuntimeError:
        raise HTTPException(status_code=503, detail="Recording playback is unavailable.")


def create_call(db: Session) -> Call:
    call = Call(started_at=datetime.now(timezone.utc))
    db.add(call)
    db.commit()
    db.refresh(call)
    return call


def finalize_call(
    db: Session,
    call_id: int,
    transcript: list[dict[str, str]],
    started_at: datetime,
    lead_details: dict[str, str] | None = None,
) -> None:
    call = db.get(Call, call_id)
    if call is None:
        return
    ended_at = datetime.now(timezone.utc)
    call.ended_at = ended_at
    call.duration_seconds = max(0, (ended_at - started_at).total_seconds())
    call.transcript = transcript
    lead = _extract_lead_details(transcript)
    if lead_details:
        lead.update({key: value for key, value in lead_details.items() if value})
    if any(lead.values()):
        call.lead_name = lead["name"]
        call.lead_company = lead["company"]
        call.lead_contact = lead["contact"]
        call.lead_preferred_time = lead["preferred_time"]
        call.lead_contact_consent = True
    generate_call_summary(db, call)
