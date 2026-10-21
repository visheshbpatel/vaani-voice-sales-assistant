import enum
import json
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import JSON, Boolean, DateTime, Enum, Float, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class CallStatus(str, enum.Enum):
    CONVERTED = "Converted"
    INTERESTED = "Interested"
    FOLLOW_UP_REQUIRED = "Follow-up Required"
    NOT_INTERESTED = "Not Interested"
    DEMO_REQUESTED = "Demo Requested"
    NO_RESPONSE = "No Response"


class SummaryGenerationStatus(str, enum.Enum):
    PENDING = "pending"
    COMPLETED = "completed"
    FAILED = "failed"


class Call(Base):
    __tablename__ = "calls"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    duration_seconds: Mapped[float | None] = mapped_column(Float)
    status: Mapped[CallStatus | None] = mapped_column(
        Enum(
            CallStatus,
            name="call_status",
            values_callable=lambda values: [v.value for v in values],
            validate_strings=True,
        ),
        nullable=True,
    )
    transcript: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list, nullable=False)
    summary: Mapped[str | None] = mapped_column(Text)
    customer_requirements: Mapped[str | None] = mapped_column(Text)
    objections: Mapped[str | None] = mapped_column(Text)
    next_steps: Mapped[str | None] = mapped_column(Text)
    recording_object_key: Mapped[str | None] = mapped_column(String(512))
    recording_content_type: Mapped[str | None] = mapped_column(String(128))
    recording_consent: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    summary_generation_status: Mapped[SummaryGenerationStatus] = mapped_column(
        Enum(
            SummaryGenerationStatus,
            name="summary_generation_status",
            values_callable=lambda values: [v.value for v in values],
            validate_strings=True,
        ),
        default=SummaryGenerationStatus.PENDING,
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        nullable=False,
    )
    lead_name: Mapped[str | None] = mapped_column(String(120))
    lead_company: Mapped[str | None] = mapped_column(String(160))
    lead_contact: Mapped[str | None] = mapped_column(String(254))
    lead_preferred_time: Mapped[str | None] = mapped_column(String(120))

    @property
    def has_recording(self) -> bool:
        return bool(self.recording_object_key)

    @property
    def summary_data(self) -> dict[str, Any] | None:
        return json.loads(self.summary) if self.summary else None
