from pydantic import BaseModel, ConfigDict, Field, HttpUrl, model_validator

USER_AGENT = "LeadRadar/0.1 (+https://github.com/NotThatBoii/LeadRadar)"
OVERPASS_URL = "https://overpass-api.de/api/interpreter"
OSM_ATTRIBUTION = "© OpenStreetMap contributors"
OSM_LICENSE = "https://opendatacommons.org/licenses/odbl/1-0/"


class RequestPolicy(BaseModel):
    interval: float = Field(default=2, ge=0, le=60)
    timeout: float = Field(default=45, gt=0, le=120)
    max_requests: int = Field(default=20, ge=1, le=100)
    max_attempts: int = Field(default=3, ge=1, le=5)
    backoff: float = Field(default=2, ge=0, le=30)
    max_bytes: int = Field(default=10_000_000, ge=1024, le=20_000_000)


class DirectoryConfig(BaseModel):
    """User-reviewed public source; selectors are CSS relative to each card."""

    model_config = ConfigDict(extra="forbid")
    name: str = Field(pattern=r"^[a-z][a-z0-9-]{1,49}$")
    start_url: HttpUrl
    policy_url: HttpUrl
    permission_note: str = Field(min_length=10)
    reviewed_on: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}$")
    automated_collection_permitted: bool
    card_selector: str = ".business"
    next_selector: str = 'a[rel="next"]'
    ready_selector: str = ".business"
    fields: dict[str, str] = Field(
        default_factory=lambda: {
            "business_name": ".name",
            "website": "a.website",
            "category": ".category",
            "city": ".city",
            "region": ".region",
            "country": ".country",
            "phone": ".phone",
            "address": ".address",
        }
    )

    @model_validator(mode="after")
    def check_permission(self):
        if not self.automated_collection_permitted:
            raise ValueError("Review source terms and obtain permission before collection")
        allowed = {
            "business_name",
            "website",
            "category",
            "city",
            "region",
            "country",
            "phone",
            "address",
        }
        if not self.fields.keys() <= allowed or "business_name" not in self.fields:
            raise ValueError(
                "Only public business fields and a business_name selector are supported"
            )
        return self
