import pathlib

import pytest
from pydantic import ValidationError

from datashare_mcp.__main__ import _render_config_error
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
    assert s.api_key.get_secret_value() == "ds_test_key"
    assert "ds_test_key" not in repr(s)
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


def test_config_error_never_echoes_the_api_key(monkeypatch):
    monkeypatch.delenv("DATASHARE_URL", raising=False)
    monkeypatch.setenv("DATASHARE_API_KEY", "ds_super_secret")
    with pytest.raises(ValidationError) as excinfo:
        Settings()
    rendered = _render_config_error(excinfo.value)
    assert "ds_super_secret" not in rendered
    assert "DATASHARE_URL" in rendered
    assert "missing" in rendered


@pytest.mark.asyncio
async def test_transport_is_pinned_to_stdio_even_when_the_environment_says_otherwise(
    monkeypatch, settings
):
    """FastMCP resolves an unspecified transport from its own settings model, whose
    env_prefix is FASTMCP_ and whose env_file is `.env` in the working directory. Either
    source could turn this stdio server into an unauthenticated HTTP listener."""
    from datashare_mcp import __main__ as entrypoint

    monkeypatch.setenv("FASTMCP_TRANSPORT", "http")
    called = {}

    class FakeMCP:
        async def run_async(self, *args, **kwargs):
            called["args"] = args
            called["kwargs"] = kwargs

    class FakeClient:
        def __init__(self):
            self.closed = False

        async def aclose(self):
            self.closed = True

    fake_client = FakeClient()
    monkeypatch.setattr(entrypoint, "build_server", lambda s: (FakeMCP(), fake_client))

    await entrypoint._serve(settings)

    assert called["kwargs"].get("transport") == "stdio" or called["args"] == ("stdio",)
    assert fake_client.closed, "the client must still be closed on the way out"


def test_ca_bundle_defaults_to_unset_and_verification_stays_on(monkeypatch):
    monkeypatch.setenv("DATASHARE_URL", "https://ds.example.com")
    monkeypatch.setenv("DATASHARE_API_KEY", "k")
    monkeypatch.delenv("DATASHARE_CA_BUNDLE", raising=False)
    s = Settings()
    assert s.ca_bundle is None
    assert s.verify_tls is True


def test_ca_bundle_is_rejected_at_startup_when_the_path_is_wrong(monkeypatch, tmp_path):
    """Otherwise the first request fails with an SSL error that names nothing."""
    monkeypatch.setenv("DATASHARE_URL", "https://ds.example.com")
    monkeypatch.setenv("DATASHARE_API_KEY", "k")
    monkeypatch.setenv("DATASHARE_CA_BUNDLE", str(tmp_path / "nope.pem"))
    with pytest.raises(ValidationError, match="no such file"):
        Settings()


def _write_bundle(path: pathlib.Path) -> pathlib.Path:
    """A real, parseable PEM bundle — certifi's, which is already a dependency of httpx."""
    import certifi

    path.write_bytes(pathlib.Path(certifi.where()).read_bytes())
    return path


@pytest.mark.asyncio
async def test_ca_bundle_becomes_the_verification_root_not_a_disable(monkeypatch, tmp_path):
    """The point of the setting: a private-CA instance is trusted by evidence, instead of
    turning verification off for every request that carries the bearer key."""
    import ssl

    from datashare_mcp.client import DatashareClient

    bundle = _write_bundle(tmp_path / "ca.pem")
    monkeypatch.setenv("DATASHARE_URL", "https://ds.example.com")
    monkeypatch.setenv("DATASHARE_API_KEY", "k")
    monkeypatch.setenv("DATASHARE_CA_BUNDLE", str(bundle))
    client = DatashareClient(Settings())
    try:
        context = client._http._transport._pool._ssl_context
        assert isinstance(context, ssl.SSLContext)
        assert context.verify_mode is ssl.CERT_REQUIRED, "a CA bundle must not weaken verification"
        assert context.get_ca_certs(), "the supplied bundle must be loaded"
    finally:
        await client.aclose()
