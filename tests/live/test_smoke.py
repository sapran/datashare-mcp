import json
import os

import pytest

from datashare_mcp.client import DatashareClient
from datashare_mcp.config import Settings

LIVE = os.getenv("DATASHARE_LIVE_TESTS") == "1"
PROJECT = os.getenv("DATASHARE_LIVE_PROJECT", "local-datashare")

pytestmark = [
    pytest.mark.live,
    pytest.mark.skipif(not LIVE, reason="DATASHARE_LIVE_TESTS!=1"),
    pytest.mark.asyncio,
]


@pytest.fixture
async def live_client():
    c = DatashareClient(Settings())
    yield c
    await c.aclose()


async def test_list_projects(live_client):
    projects = await live_client.list_projects()
    assert isinstance(projects, list)
    assert len(projects) >= 1
    names = [p.get("name") for p in projects]
    assert PROJECT in names, f"Expected project {PROJECT} in {names}"


async def test_mapping_parses(live_client):
    mapping = await live_client.get_mapping(project=PROJECT)
    assert isinstance(mapping, dict)
    assert mapping  # non-empty
    json.dumps(mapping)  # serializable


async def test_search_match_all(live_client):
    res = await live_client.search(project=PROJECT, query={"query": {"match_all": {}}, "size": 1})
    assert "hits" in res
