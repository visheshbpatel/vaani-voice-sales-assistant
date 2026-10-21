import logging
from datetime import UTC, datetime
from pathlib import Path

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request
from fastapi.responses import FileResponse
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.config import settings
from app.database import get_db
from app.models.call import Call, SummaryGenerationStatus
from app.schemas.call import CallHistory, CallListItem
from app.security import require_history_access, valid_call_token, valid_password
from app.services.call_summary import (
    SummaryGenerationError,
    generate_summary,
    summary_json,
)
from app.services.recording_storage import save_recording, signed_recording_url

router = APIRouter(prefix="/api/calls", tags=["calls"])
logger = logging.getLogger(__name__)

@router.get("", response_model=list[CallListItem])
def list_calls(
    limit: int = Query(default=50, ge=1, le=200),
    x_history_password: str | None = Header(default=None),
    db: Session = Depends(get_db),
) -> list[Call]:
    require_history_access(None, x_history_password, None, list_access=True)
    try:
        return list(
            db.scalars(select(Call).order_by(Call.started_at.desc()).limit(limit)).all()
        )
    except SQLAlchemyError:
        logger.exception("Could not retrieve call history.")
        raise HTTPException(status_code=503, detail="Call history is temporarily unavailable.")


@router.get("/{call_id}", response_model=CallHistory)
def get_call(
    call_id: int,
    x_history_password: str | None = Header(default=None),
    x_call_token: str | None = Header(default=None),
    db: Session = Depends(get_db),
) -> Call:
    require_history_access(call_id, x_history_password, x_call_token)
    try:
        call = db.get(Call, call_id)
    except SQLAlchemyError:
        logger.exception("Could not retrieve call record.")
        raise HTTPException(status_code=503, detail="Call history is temporarily unavailable.")
    if call is None:
        raise HTTPException(status_code=404, detail="Call record not found.")
    return call


def generate_call_summary(db: Session, call: Call) -> Call:
    try:
        summary = generate_summary(call.transcript)
    except SummaryGenerationError:
        call.summary_generation_status = SummaryGenerationStatus.FAILED
        call.status = None
        call.summary = None
    else:
        call.summary = summary_json(summary)
        call.status = summary.status
        call.customer_requirements = "\n".join(summary.needs) or None
        call.objections = "\n".join(summary.objections) or None
        call.next_steps = "\n".join(summary.next_steps) or None
        for field in (
            "lead_name",
            "lead_company",
            "lead_contact",
            "lead_preferred_time",
        ):
            value = getattr(summary, field).strip()
            if value and not getattr(call, field):
                setattr(call, field, value)
        call.summary_generation_status = SummaryGenerationStatus.COMPLETED
    db.commit()
    db.refresh(call)
    return call


@router.post("/{call_id}/summary/retry", response_model=CallHistory)
def retry_summary(
    call_id: int,
    x_history_password: str | None = Header(default=None),
    x_call_token: str | None = Header(default=None),
    db: Session = Depends(get_db),
) -> Call:
    require_history_access(
        call_id, x_history_password, x_call_token, require_credentials=True
    )
    call = db.get(Call, call_id)
    if call is None:
        raise HTTPException(status_code=404, detail="Call record not found.")
    if call.ended_at is None:
        raise HTTPException(status_code=409, detail="Call has not ended.")
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
    history_password = request.headers.get("x-history-password")
    call_token = request.headers.get("x-call-token")
    if not valid_password(history_password):
        if not call_token:
            raise HTTPException(status_code=401, detail="missing call token")
        if not valid_call_token(call_id, call_token):
            raise HTTPException(status_code=401, detail="invalid call token")
    call = db.get(Call, call_id)
    if call is None:
        raise HTTPException(status_code=404, detail="Call record not found.")
    if not call.recording_consent:
        raise HTTPException(status_code=400, detail="Recording consent was not provided.")
    if call.recording_object_key:
        raise HTTPException(status_code=409, detail="A recording already exists for this call.")
    content_type = request.headers.get("content-type", "").split(";", 1)[0].strip().lower()
    if content_type not in {"audio/webm", "audio/ogg"}:
        raise HTTPException(status_code=415, detail="Unsupported recording format.")
    content_length = request.headers.get("content-length")
    if content_length and int(content_length) > settings.RECORDING_MAX_BYTES:
        raise HTTPException(status_code=413, detail="Recording is empty or too large.")
    chunks: list[bytes] = []
    size = 0
    async for chunk in request.stream():
        size += len(chunk)
        if size > settings.RECORDING_MAX_BYTES:
            raise HTTPException(status_code=413, detail="Recording is empty or too large.")
        chunks.append(chunk)
    content = b"".join(chunks)
    if not content:
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
    if not settings.ALLOW_LOCAL_RECORDINGS or settings.HISTORY_PASSWORD:
        raise HTTPException(status_code=404, detail="Recording is unavailable.")
    root = Path("recordings").resolve()
    path = (root / object_key).resolve()
    if root not in path.parents or not path.is_file():
        raise HTTPException(status_code=404, detail="Recording is unavailable.")
    return FileResponse(path)


@router.get("/{call_id}/recording")
async def get_recording_url(
    call_id: int,
    request: Request,
    db: Session = Depends(get_db),
):
    require_history_access(
        call_id,
        request.headers.get("x-history-password"),
        request.headers.get("x-call-token"),
    )
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
    call = Call(started_at=datetime.now(UTC))
    db.add(call)
    db.commit()
    db.refresh(call)
    return call


def finalize_call(
    db: Session,
    call_id: int,
    transcript: list[dict[str, str]],
    started_at: datetime | None = None,
) -> None:
    call = db.get(Call, call_id)
    if call is None:
        return
    ended_at = datetime.now(UTC)
    stored_started_at = call.started_at
    if stored_started_at.tzinfo is None:
        stored_started_at = stored_started_at.replace(tzinfo=UTC)
    call.ended_at = ended_at
    call.duration_seconds = max(0, (ended_at - stored_started_at).total_seconds())
    call.transcript = transcript
    db.commit()
