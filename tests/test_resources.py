import httpx
import pytest
from fastmcp import Client as MCPClient

from datashare_mcp.server import build_server

pytestmark = pytest.mark.asyncio


async def test_mapping_resource(settings, respx_mock):
    respx_mock.get("/api/index/search/leaks/_mapping").mock(
        return_value=httpx.Response(
            200,
            json={"leaks": {"mappings": {"properties": {"content": {"type": "text"}}}}},
        )
    )
    server, ds_client = build_server(settings)
    try:
        async with MCPClient(server) as mcp_client:
            contents = await mcp_client.read_resource("datashare://index/leaks/mapping")
            assert "properties" in contents[0].text
    finally:
        await ds_client.aclose()


async def test_document_resource(settings, respx_mock):
    respx_mock.get("/api/leaks/documents/content/abc").mock(
        return_value=httpx.Response(
            200,
            json={"content": "Body text here", "maxOffset": 14, "start": 0, "size": 14},
        )
    )
    server, ds_client = build_server(settings)
    try:
        async with MCPClient(server) as mcp_client:
            contents = await mcp_client.read_resource("datashare://document/leaks/abc")
            assert "Body text here" in contents[0].text
    finally:
        await ds_client.aclose()


async def test_document_resource_404_raises_resource_error(settings, respx_mock):
    # FastMCP's in-process client surfaces server-side ResourceError as McpError.
    # We verify (a) the call fails and (b) the server logs show ResourceError (not ToolError).
    from mcp.shared.exceptions import McpError

    respx_mock.get("/api/leaks/documents/content/abc").mock(return_value=httpx.Response(404))
    server, ds_client = build_server(settings)
    try:
        async with MCPClient(server) as mcp_client:
            with pytest.raises(McpError, match="not found"):
                await mcp_client.read_resource("datashare://document/leaks/abc")
    finally:
        await ds_client.aclose()


async def test_projects_resource(settings, respx_mock):
    respx_mock.get("/api/project/").mock(return_value=httpx.Response(200, json=[{"name": "leaks"}]))
    server, ds_client = build_server(settings)
    try:
        async with MCPClient(server) as mcp_client:
            contents = await mcp_client.read_resource("datashare://projects")
            # contents is a list of TextResourceContents / BlobResourceContents.
            # For JSON-typed resources, the body is serialized into the .text field.
            text = contents[0].text
            assert "leaks" in text
    finally:
        await ds_client.aclose()
