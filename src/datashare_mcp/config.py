from pathlib import Path

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="DATASHARE_",
        case_sensitive=False,
        extra="ignore",
    )

    url: str = Field(..., description="Base URL of the datashare instance, no trailing slash.")
    api_key: SecretStr = Field(..., description="Bearer API key (datashare api-key create <user>).")
    timeout_secs: float = Field(
        30, ge=1, description="Per-operation HTTP timeout (connect, read, write, pool)."
    )
    deadline_secs: float = Field(
        120,
        ge=1,
        description=(
            "Total wall-clock budget for one Datashare call. timeout_secs bounds the wait "
            "for the next chunk, not the whole response, so a slow drip needs this too."
        ),
    )
    verify_tls: bool = Field(
        True,
        description=(
            "Verify TLS certificates. Setting false disables validation for every request, "
            "each of which carries the bearer key — acceptable only on loopback or an "
            "otherwise trusted link. For a self-signed remote instance set ca_bundle instead."
        ),
    )
    ca_bundle: str | None = Field(
        None,
        description=(
            "Path to a PEM CA bundle used to verify the instance. Lets a self-signed or "
            "private-CA instance be trusted without turning verification off wholesale."
        ),
    )
    max_search_size: int = Field(
        200, ge=1, description="Upper bound clamped onto a search body's `size` and `from`."
    )
    max_content_bytes: int = Field(
        1_000_000,
        ge=1024,
        description=(
            "Ceiling on extracted text fetched in one get_document_content call. "
            "Larger documents are truncated; page through them with offset/limit."
        ),
    )

    @field_validator("url")
    @classmethod
    def _strip_trailing_slash(cls, v: str) -> str:
        return v.rstrip("/")

    @field_validator("ca_bundle")
    @classmethod
    def _ca_bundle_must_exist(cls, v: str | None) -> str | None:
        # Fail at startup with the path in hand, rather than on the first request with an
        # SSL error that names nothing.
        if v is not None and not Path(v).is_file():
            raise ValueError(f"ca_bundle: no such file: {v}")
        return v
