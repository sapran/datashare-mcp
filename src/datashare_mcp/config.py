from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="DATASHARE_",
        case_sensitive=False,
        extra="ignore",
    )

    url: str = Field(..., description="Base URL of the datashare instance, no trailing slash.")
    api_key: str = Field(..., description="Bearer API key (datashare api-key create <user>).")
    timeout_secs: float = Field(30, ge=1, description="Per-request HTTP timeout.")
    verify_tls: bool = Field(True, description="Verify TLS certs (set false for self-signed).")

    @field_validator("url")
    @classmethod
    def _strip_trailing_slash(cls, v: str) -> str:
        return v.rstrip("/")
