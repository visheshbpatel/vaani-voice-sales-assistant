import asyncio
import hashlib
import logging
import threading
from datetime import UTC, datetime

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
from app.services import recording_storage
from app.services.recording_storage import (
    RecordingStorageError,
    delete_recording,
    save_recording,
    signed_recording_url,
)

router = APIRouter(prefix="/api/calls", tags=["calls"])
logger = logging.getLogger(__name__)
_summary_locks: dict[int, threading.Lock] = {}
_summary_locks_guard = threading.Lock()
_recording_locks: dict[int, threading.Lock] = {}
_recording_locks_guard = threading.Lock()


class SummaryGenerationInProgress(RuntimeError):
    pass


def _summary_lock(call_id: int) -> threading.Lock:
    with _summary_locks_guard:
        return _summary_locks.setdefault(call_id, threading.Lock())


def _recording_lock(call_id: int) -> threading.Lock:
    with _recording_locks_guard:
        return _recording_locks.setdefault(call_id, threading.Lock())


async def _read_recording(request: Request) -> bytes:
    content_length = request.headers.get("content-length")
    if content_length:
        try:
            declared_length = int(content_length)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail="Invalid recording length.") from exc
        if declared_length < 0:
            raise HTTPException(status_code=400, detail="Invalid recording length.")
        if declared_length > settings.RECORDING_MAX_BYTES:
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
    return content

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


def generate_call_summary(db: Session, call: Call, *, wait: bool = True) -> Call:
    lock = _summary_lock(call.id)
    if not lock.acquire(blocking=wait):
        raise SummaryGenerationInProgress
    try:
        if call.ended_at is None:
            raise SummaryGenerationError("Call has not ended.")
        call.summary_generation_status = SummaryGenerationStatus.PENDING
        db.commit()
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
    finally:
        lock.release()


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
        return generate_call_summary(db, call, wait=False)
    except SummaryGenerationInProgress:
        raise HTTPException(
            status_code=409,
            detail="Summary generation is already in progress.",
        )
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
    recording_lock = _recording_lock(call_id)
    await asyncio.to_thread(recording_lock.acquire)
    try:
        call = db.get(Call, call_id)
        if call is None:
            raise HTTPException(status_code=404, detail="Call record not found.")
        if not call.recording_consent:
            raise HTTPException(status_code=400, detail="Recording consent was not provided.")
        content_type = request.headers.get("content-type", "").split(";", 1)[0].strip().lower()
        if content_type not in {"audio/webm", "audio/ogg"}:
            raise HTTPException(status_code=415, detail="Unsupported recording format.")
        content = await _read_recording(request)
        content_hash = hashlib.sha256(content).hexdigest()
        if call.recording_object_key:
            if call.recording_sha256 == content_hash:
                return {"saved": True, "content_type": call.recording_content_type}
            raise HTTPException(status_code=409, detail="A recording already exists for this call.")
        object_key = None
        try:
            object_key = await save_recording(call_id, content, content_type)
            call.recording_object_key = object_key
            call.recording_content_type = content_type
            call.recording_sha256 = content_hash
            db.commit()
        except (SQLAlchemyError, RuntimeError):
            db.rollback()
            if object_key:
                await delete_recording(object_key)
            logger.exception("Could not save call recording.")
            raise HTTPException(status_code=503, detail="Recording could not be saved.")
        return {"saved": True, "content_type": content_type}
    finally:
        recording_lock.release()


@router.get("/local/{object_key:path}")
def get_local_recording(object_key: str):
    if not settings.ALLOW_LOCAL_RECORDINGS or settings.HISTORY_PASSWORD:
        raise HTTPException(status_code=404, detail="Recording is unavailable.")
    root = recording_storage.LOCAL_RECORDINGS.resolve()
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
    try:
        call = db.get(Call, call_id)
    except SQLAlchemyError:
        logger.exception("Could not retrieve recording metadata.")
        raise HTTPException(
            status_code=503, detail="Recording metadata is temporarily unavailable."
        )
    if call is None:
        raise HTTPException(status_code=404, detail="Call record not found.")
    if not call.recording_object_key:
        logger.warning("Recording metadata is missing for call_id=%d.", call_id)
        raise HTTPException(status_code=404, detail="Recording is unavailable.")
    try:
        return {"url": await signed_recording_url(call.recording_object_key)}
    except RecordingStorageError as exc:
        if exc.missing_object:
            logger.warning(
                "Recording object is missing for call_id=%d; key=%s.",
                call_id,
                call.recording_object_key,
            )
        else:
            logger.exception(
                "Recording storage error while preparing playback for call_id=%d.",
                call_id,
            )
        raise HTTPException(status_code=503, detail="Recording playback is unavailable.")
    except RuntimeError:
        logger.exception(
            "Recording playback configuration error for call_id=%d.", call_id
        )
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
