from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.models.call import CallStatus


class CallHistory(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    started_at: datetime
    ended_at: datetime | None
    duration_seconds: float | None
    status: CallStatus | None
    transcript: list[dict[str, Any]]
    summary: str | None
    customer_requirements: str | None
    objections: str | None
    next_steps: str | None
    recording_object_key: str | None
    recording_content_type: str | None
    recording_consent: bool
    summary_generation_status: str
    created_at: datetime
    lead_name: str | None
    lead_company: str | None
    lead_contact: str | None
    lead_preferred_time: str | None
    lead_contact_consent: bool | None


class LeadUpdate(BaseModel):
    name: str | None = Field(default=None, max_length=120)
    company: str | None = Field(default=None, max_length=160)
    contact: str | None = Field(default=None, max_length=254)
    preferred_time: str | None = Field(default=None, max_length=120)
    consent: bool = False

    @field_validator("name", "company", "contact", "preferred_time")
    @classmethod
    def trim_and_reject_blank(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip()
        if not value:
            raise ValueError("Details cannot be blank.")
        return value
