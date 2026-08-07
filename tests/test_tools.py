import httpx
import pytest
from fastmcp import Client as MCPClient

from datashare_mcp.server import build_server

pytestmark = pytest.mark.asyncio


async def test_list_projects_tool(settings, respx_mock):
    respx_mock.get("/api/project/").mock(
        return_value=httpx.Response(200, json=[{"name": "leaks"}, {"name": "panama"}])
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
            json={"hits": {"total": {"value": 1}, "hits": [{"_id": "abc"}]}},
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
        return_value=httpx.Response(
            200, json={"content": "Full text", "maxOffset": 9, "start": 0, "size": 9}
        )
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
            200, json={"content": "X", "maxOffset": 1000, "start": 100, "size": 50}
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
            json={
                "hits": {"total": {"value": 100}, "hits": []},
                "aggregations": {
                    "by_language": {"buckets": [{"key": "ENGLISH", "doc_count": 80}]},
                    "min_date": {"value": 1000000},
                    "max_date": {"value": 2000000},
                },
            },
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
                json={
                    "hits": {"total": {"value": 100}, "hits": []},
                    "aggregations": {
                        "by_type": {
                            "buckets": [
                                {"key": "application/pdf", "doc_count": 50},
                                {"key": "application/msword", "doc_count": 50},
                            ]
                        }
                    },
                },
            )
        return httpx.Response(200, json={"hits": {"total": {"value": 100}, "hits": []}})

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
            json={
                "hits": {"total": {"value": 100}, "hits": []},
                "aggregations": {
                    "by_year": {
                        "buckets": [
                            {
                                "key": 1609459200000,
                                "key_as_string": "yyyy1609459200000",
                                "doc_count": 30,
                            },
                            {
                                "key": 1640995200000,
                                "key_as_string": "yyyy1640995200000",
                                "doc_count": 70,
                            },
                        ]
                    }
                },
            },
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
            json={
                "hits": {"total": {"value": 100}, "hits": []},
                "aggregations": {
                    "by_language": {"buckets": [{"key": "ENGLISH", "doc_count": 80}]},
                    "min_date": {"value": 1609459200000},
                    "max_date": {"value": 1640995200000},
                    "by_type": {"buckets": [{"key": "application/pdf", "doc_count": 50}]},
                    "by_year": {
                        "buckets": [
                            {
                                "key": 1609459200000,
                                "key_as_string": "yyyy1609459200000",
                                "doc_count": 30,
                            }
                        ]
                    },
                },
            },
        )
    )
    respx_mock.get("/api/leaks/documents/content/abc").mock(
        return_value=httpx.Response(
            200, json={"content": "test", "maxOffset": 4, "offset": 0, "limit": 0}
        )
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
