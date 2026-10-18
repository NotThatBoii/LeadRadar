from datetime import UTC, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


def utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="microseconds")


class RawBusiness(BaseModel):
    model_config = ConfigDict(extra="forbid")
    business_name: str | None = None
    website: str | None = None
    category: str | None = None
    city: str | None = None
    region: str | None = None
    country: str | None = None
    phone: str | None = None
    address: str | None = None
    location_key: str | None = None
    source: str = Field(min_length=1)
    source_record_id: str = Field(min_length=1)
    source_url: str = Field(min_length=1)
    source_kind: Literal["live", "fixture"] = "live"
    attribution: str | None = None
    license_url: str | None = None


class Business(RawBusiness):
    business_name: str = Field(min_length=1, max_length=500)
    domain: str | None = None
    validation_status: Literal["valid", "partial", "warning"]
    warnings: list[str] = Field(default_factory=list)
    original_values: dict = Field(default_factory=dict)
    field_provenance: dict = Field(default_factory=dict)
    identity_key: str

    @field_validator("business_name")
    @classmethod
    def require_visible_name(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("business_name cannot be blank")
        return value


class RunReport(BaseModel):
    id: int | None = None
    source: str
    started_at: str = Field(default_factory=utc_now)
    ended_at: str | None = None
    status: Literal["running", "success", "partial", "failed", "interrupted"] = "running"
    processed: int = 0
    new: int = 0
    updated: int = 0
    duplicates: int = 0
    failed: int = 0
    requests: int = 0
    errors: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)

    def warn(self, message: str) -> None:
        # Keep reports bounded for large/malformed sources.
        if message not in self.warnings and len(self.warnings) < 100:
            self.warnings.append(message)
