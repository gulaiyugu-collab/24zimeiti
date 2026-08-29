from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


AnalysisStatus = Literal["completed", "needs_input", "partial", "unsupported"]
AnalysisMode = Literal["quick", "full"]
AnalysisStrategy = Literal["multi_agent", "single_model"]
ProductRelevanceOverride = Literal["has_product", "no_product", "needs_confirmation"]
PlatformStatus = Literal["active", "planned"]
ASRMode = Literal["auto", "external", "local", "disabled"]
TranscriptionStatus = Literal["completed", "unavailable", "failed"]
AcquisitionStatus = Literal["queued", "processing", "completed", "needs_input", "failed"]


class ProductInput(BaseModel):
    """Structured product facts supplied by the client, not inferred by the app."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    name: str | None = Field(default=None, max_length=500)
    brand: str | None = Field(default=None, max_length=500)
    sku: str | None = Field(default=None, max_length=500)
    category: str | None = Field(default=None, max_length=500)
    selling_points: list[str] = Field(default_factory=list, max_length=50)
    specifications: dict[str, Any] = Field(default_factory=dict)
    included_items: list[str] = Field(default_factory=list, max_length=100)
    target_audience: list[str] = Field(default_factory=list, max_length=50)
    approved_claims: list[str] = Field(default_factory=list, max_length=100)
    evidence_urls: list[str] = Field(default_factory=list, max_length=100)

    @field_validator(
        "name",
        "brand",
        "sku",
        "category",
    )
    @classmethod
    def empty_product_text_is_none(cls, value: str | None) -> str | None:
        return value or None


class MarketSelection(BaseModel):
    """Future localization input; v0.2 records it but does not transform content."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    region: str | None = Field(default=None, max_length=200)
    country: str | None = Field(default=None, max_length=200)
    language: str | None = Field(default=None, max_length=200)

    @field_validator("region", "country", "language")
    @classmethod
    def empty_market_text_is_none(cls, value: str | None) -> str | None:
        return value or None


class ASRPreferences(BaseModel):
    """Provider selection for the explicit media transcription endpoint."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    mode: ASRMode = "auto"


class AnalyzeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    url: str = Field(min_length=1, max_length=2048)
    analysis_mode: AnalysisMode = "full"
    analysis_strategy: AnalysisStrategy = "multi_agent"
    transcript: str | None = Field(default=None, max_length=50_000)
    product_context: str | None = Field(default=None, max_length=10_000)
    product: ProductInput | None = None
    product_relevance_override: ProductRelevanceOverride | None = None
    market: MarketSelection = Field(default_factory=MarketSelection)
    asr: ASRPreferences = Field(default_factory=ASRPreferences)

    @field_validator("transcript", "product_context")
    @classmethod
    def empty_text_is_none(cls, value: str | None) -> str | None:
        return value or None


class AcquisitionJobRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    url: str = Field(min_length=1, max_length=2048)
    item_limit: int = Field(default=1, ge=1, le=50)
    force_refresh: bool = False


class AcquisitionAnalysisRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    analysis_mode: AnalysisMode = "quick"
    analysis_strategy: AnalysisStrategy = "multi_agent"
    product_context: str | None = Field(default=None, max_length=10_000)
    product: ProductInput | None = None
    product_relevance_override: ProductRelevanceOverride | None = None
    market: MarketSelection = Field(default_factory=MarketSelection)

    @field_validator("product_context")
    @classmethod
    def empty_product_context_is_none(cls, value: str | None) -> str | None:
        return value or None


class AcquisitionJobResponse(BaseModel):
    job_id: str
    status: AcquisitionStatus
    platform: str
    message: str
    progress: dict[str, Any]
    cache_hit: bool = False
    created_at: str
    updated_at: str
    manifest_url: str | None = None
    missing: list[str] = Field(default_factory=list)
    artifacts: list[dict[str, Any]] = Field(default_factory=list)
    worker_pid: int | None = None
    error_type: str | None = None


class AnalysisResponse(BaseModel):
    status: AnalysisStatus
    platform: str
    message: str
    source: dict[str, Any]
    report: dict[str, Any] | None = None
    diagnostics: dict[str, Any] = Field(default_factory=dict)
    missing: list[str] = Field(default_factory=list)
    next_action: dict[str, Any] | None = None


class TranscriptionResponse(BaseModel):
    status: TranscriptionStatus
    message: str
    transcript: str | None = None
    provider: str | None = None
    model: str | None = None
    language: str | None = None
    segments: list[dict[str, Any]] | None = None
    segments_status: str
    source: dict[str, Any]
    confidence: float | None = None
    confidence_status: str


class PlatformInfo(BaseModel):
    id: str
    name: str
    status: PlatformStatus
    description: str


class PlatformsResponse(BaseModel):
    platforms: list[PlatformInfo]


class DemoResponse(BaseModel):
    sample_input: dict[str, str]
    result: AnalysisResponse


class HealthResponse(BaseModel):
    status: Literal["ok"]
    service: str
    version: str
    paid_content_enabled: bool = False


class DouyinBrowserExportRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    browser_id: str | None = Field(default=None, max_length=40)
    profile_mode: Literal["existing", "temporary"] = "existing"
    timeout_seconds: int = Field(default=180, ge=30, le=300)


class DouyinDownloadImportRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    since_epoch_ms: int = Field(default=0, ge=0)
