import pytest
from pydantic import ValidationError

from datashare_mcp.config import Settings


def test_required_fields(monkeypatch):
    monkeypatch.delenv("DATASHARE_URL", raising=False)
    monkeypatch.delenv("DATASHARE_API_KEY", raising=False)
    with pytest.raises(ValidationError):
        Settings()


def test_loads_from_env(monkeypatch):
    monkeypatch.setenv("DATASHARE_URL", "http://localhost:8080")
    monkeypatch.setenv("DATASHARE_API_KEY", "ds_test_key")
    s = Settings()
    assert str(s.url) == "http://localhost:8080"
    assert s.api_key == "ds_test_key"
    assert s.timeout_secs == 30
    assert s.verify_tls is True


def test_strips_trailing_slash(monkeypatch):
    monkeypatch.setenv("DATASHARE_URL", "http://localhost:8080/")
    monkeypatch.setenv("DATASHARE_API_KEY", "k")
    s = Settings()
    assert str(s.url) == "http://localhost:8080"


def test_optional_overrides(monkeypatch):
    monkeypatch.setenv("DATASHARE_URL", "https://ds.example.com")
    monkeypatch.setenv("DATASHARE_API_KEY", "k")
    monkeypatch.setenv("DATASHARE_TIMEOUT_SECS", "60")
    monkeypatch.setenv("DATASHARE_VERIFY_TLS", "false")
    s = Settings()
    assert s.timeout_secs == 60
    assert s.verify_tls is False
