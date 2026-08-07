import httpx
import pytest
from fastmcp import Client as MCPClient

from datashare_mcp.server import build_server
from tests.shapes import (
    content_payload,
    date_range_aggs,
    language_buckets,
    project_list,
    search_hit,
    search_payload,
    type_buckets,
    year_buckets,
)

pytestmark = pytest.mark.asyncio


async def test_list_projects_tool(settings, respx_mock):
    respx_mock.get("/api/project/").mock(
        return_value=httpx.Response(200, json=project_list("leaks", "panama"))
    )
    server, ds_client = build_server(settings)
    try:
        async with MCPClient(server) as mcp_client:
            result = await mcp_client.call_tool("list_projects", {})
            assert [p["name"] for p in result.data] == ["leaks", "panama"]
    finally:
        await ds_client.aclose()


async def test_search_documents_tool(settings, respx_mock):
    captured = {}

    def handler(request):
        import json as _json

        captured.update(_json.loads(request.content))
        return httpx.Response(
            200,
            json=search_payload(hits=[search_hit(id="abc")]),
        )

    respx_mock.post("/api/index/search/leaks/_search").mock(side_effect=handler)

    server, ds_client = build_server(settings)
    try:
        async with MCPClient(server) as mcp_client:
            query = {"query": {"match": {"content": "kremlin"}}, "size": 1}
            result = await mcp_client.call_tool(
                "search_documents", {"project": "leaks", "query": query}
            )
            assert captured == query
            assert result.data["hits"]["total"]["value"] == 1
    finally:
        await ds_client.aclose()


async def test_get_document_metadata_tool(settings, respx_mock):
    respx_mock.get("/api/leaks/documents/abc").mock(
        return_value=httpx.Response(
            200, json={"id": "abc", "path": "/data/x.pdf", "contentType": "application/pdf"}
        )
    )
    server, ds_client = build_server(settings)
    try:
        async with MCPClient(server) as mcp_client:
            result = await mcp_client.call_tool(
                "get_document_metadata", {"project": "leaks", "doc_id": "abc"}
            )
            assert result.data["id"] == "abc"
            assert result.data["contentType"] == "application/pdf"
    finally:
        await ds_client.aclose()


async def test_get_document_content_tool_full(settings, respx_mock):
    respx_mock.get("/api/leaks/documents/content/abc").mock(
        return_value=httpx.Response(200, json=content_payload(content="Full text"))
    )
    server, ds_client = build_server(settings)
    try:
        async with MCPClient(server) as mcp_client:
            result = await mcp_client.call_tool(
                "get_document_content", {"project": "leaks", "doc_id": "abc"}
            )
            assert result.data["content"] == "Full text"
            assert result.data["maxOffset"] == 9
    finally:
        await ds_client.aclose()


async def test_get_document_content_tool_range(settings, respx_mock):
    route = respx_mock.get(
        "/api/leaks/documents/content/abc",
        params={"offset": "100", "limit": "50"},
    ).mock(
        return_value=httpx.Response(
            200, json=content_payload(content="X", max_offset=1000, offset=100, limit=50)
        )
    )

    server, ds_client = build_server(settings)
    try:
        async with MCPClient(server) as mcp_client:
            await mcp_client.call_tool(
                "get_document_content",
                {"project": "leaks", "doc_id": "abc", "offset": 100, "limit": 50},
            )
        assert route.called
    finally:
        await ds_client.aclose()


async def test_get_document_content_half_range_surfaces_tool_error(settings, respx_mock):
    from fastmcp.exceptions import ToolError

    server, ds_client = build_server(settings)
    try:
        async with MCPClient(server) as mcp_client:
            with pytest.raises(ToolError, match="together"):
                await mcp_client.call_tool(
                    "get_document_content",
                    {"project": "leaks", "doc_id": "abc", "offset": 0},
                )
    finally:
        await ds_client.aclose()


async def test_get_project_overview_tool(settings, respx_mock):
    respx_mock.post("/api/index/search/leaks/_search").mock(
        return_value=httpx.Response(
            200,
            json=search_payload(
                hits=[],
                total=100,
                aggregations=language_buckets(("ENGLISH", 80))
                | date_range_aggs(min_ms=1000000, max_ms=2000000),
            ),
        )
    )

    server, ds_client = build_server(settings)
    try:
        async with MCPClient(server) as mcp_client:
            result = await mcp_client.call_tool("get_project_overview", {"project": "leaks"})
            assert result.data["totalDocuments"] == 100
            assert len(result.data["languageDistribution"]) == 1
    finally:
        await ds_client.aclose()


async def test_get_document_type_distribution_tool(settings, respx_mock):
    def handler(request):
        import json as _json

        body = _json.loads(request.content)
        if "by_type" in str(body):
            return httpx.Response(
                200,
                json=search_payload(
                    hits=[],
                    total=100,
                    aggregations=type_buckets(("application/pdf", 50), ("application/msword", 50)),
                ),
            )
        return httpx.Response(200, json=search_payload(hits=[], total=100))

    respx_mock.post("/api/index/search/leaks/_search").mock(side_effect=handler)

    server, ds_client = build_server(settings)
    try:
        async with MCPClient(server) as mcp_client:
            result = await mcp_client.call_tool(
                "get_document_type_distribution", {"project": "leaks"}
            )
            assert result.data["totalDocuments"] == 100
            type_dict = {t["type"]: t for t in result.data["typeDistribution"]}
            assert "PDF Documents" in type_dict
            assert "Word Documents" in type_dict
    finally:
        await ds_client.aclose()


async def test_get_temporal_distribution_tool(settings, respx_mock):
    respx_mock.post("/api/index/search/leaks/_search").mock(
        return_value=httpx.Response(
            200,
            json=search_payload(
                hits=[], total=100, aggregations=year_buckets((2021, 30), (2022, 70))
            ),
        )
    )

    server, ds_client = build_server(settings)
    try:
        async with MCPClient(server) as mcp_client:
            result = await mcp_client.call_tool("get_temporal_distribution", {"project": "leaks"})
            assert result.data["totalDocuments"] == 100
            years = [y["year"] for y in result.data["yearlyDistribution"]]
            assert 2021 in years
    finally:
        await ds_client.aclose()


async def test_get_project_summary_tool(settings, respx_mock):
    respx_mock.post("/api/index/search/leaks/_search").mock(
        return_value=httpx.Response(
            200,
            json=search_payload(
                hits=[],
                total=100,
                aggregations=language_buckets(("ENGLISH", 80))
                | date_range_aggs(min_ms=1609459200000, max_ms=1640995200000)
                | type_buckets(("application/pdf", 50))
                | year_buckets((2021, 30)),
            ),
        )
    )
    respx_mock.get("/api/leaks/documents/content/abc").mock(
        return_value=httpx.Response(200, json=content_payload(content="test"))
    )

    server, ds_client = build_server(settings)
    try:
        async with MCPClient(server) as mcp_client:
            result = await mcp_client.call_tool(
                "get_project_summary", {"project": "leaks", "format": "json"}
            )
            assert "metadata" in result.data
            assert result.data["overview"]["totalDocuments"] == 100

            result_md = await mcp_client.call_tool(
                "get_project_summary", {"project": "leaks", "format": "markdown"}
            )
            assert "markdown" in result_md.data
            assert "# Project Summary" in result_md.data["markdown"]
    finally:
        await ds_client.aclose()
