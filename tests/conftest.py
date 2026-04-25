import pytest
import respx

from datashare_mcp.client import DatashareClient
from datashare_mcp.config import Settings


@pytest.fixture
def settings(monkeypatch) -> Settings:
    monkeypatch.setenv("DATASHARE_URL", "http://datashare.test")
    monkeypatch.setenv("DATASHARE_API_KEY", "test_key")
    return Settings()


@pytest.fixture
def respx_mock():
    with respx.mock(base_url="http://datashare.test", assert_all_called=False) as m:
        yield m


@pytest.fixture
async def client(settings) -> DatashareClient:
    c = DatashareClient(settings)
    yield c
    await c.aclose()
