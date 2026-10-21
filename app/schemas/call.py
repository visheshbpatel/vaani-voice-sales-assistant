from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, field_validator

from app.models.call import CallStatus


def _utc_datetime(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value


class CallListItem(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    started_at: datetime
    ended_at: datetime | None
    duration_seconds: float | None
    status: CallStatus | None
    summary_generation_status: str
    recording_consent: bool
    has_recording: bool

    @field_validator("started_at", "ended_at", mode="before")
    @classmethod
    def normalize_datetime(cls, value: datetime | None) -> datetime | None:
        return _utc_datetime(value) if value is not None else None


class CallHistory(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    started_at: datetime
    ended_at: datetime | None
    duration_seconds: float | None
    status: CallStatus | None
    transcript: list[dict[str, Any]]
    summary_data: dict[str, Any] | None
    customer_requirements: str | None
    objections: str | None
    next_steps: str | None
    recording_consent: bool
    has_recording: bool
    summary_generation_status: str
    created_at: datetime
    lead_name: str | None
    lead_company: str | None
    lead_contact: str | None
    lead_preferred_time: str | None

    @field_validator("started_at", "ended_at", "created_at", mode="before")
    @classmethod
    def normalize_datetime(cls, value: datetime | None) -> datetime | None:
        return _utc_datetime(value) if value is not None else None
