from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

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
    lead_contact_consent: bool | None

    @field_validator("started_at", "ended_at", "created_at", mode="before")
    @classmethod
    def normalize_datetime(cls, value: datetime | None) -> datetime | None:
        return _utc_datetime(value) if value is not None else None


class LeadUpdate(BaseModel):
    name: str | None = Field(default=None, max_length=120)
    company: str | None = Field(default=None, max_length=160)
    contact: str | None = Field(default=None, max_length=254)
    preferred_time: str | None = Field(default=None, max_length=120)
    consent: bool = False

    @field_validator("name", "company", "contact", "preferred_time", mode="before")
    @classmethod
    def trim_blank_to_none(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip()
        return value or None

    @model_validator(mode="after")
    def require_consent_for_details(self) -> "LeadUpdate":
        if not self.consent and any(
            detail is not None
            for detail in (self.name, self.company, self.contact, self.preferred_time)
        ):
            raise ValueError("Tick the consent box to save these details.")
        return self
