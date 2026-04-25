import httpx
import pytest
from fastmcp.exceptions import ResourceError, ToolError

from datashare_mcp.errors import raise_for_status


def _resp(status: int, body: str = "", json: dict | None = None) -> httpx.Response:
    if json is not None:
        return httpx.Response(status, json=json, request=httpx.Request("GET", "http://x"))
    return httpx.Response(status, text=body, request=httpx.Request("GET", "http://x"))


def test_2xx_no_raise():
    raise_for_status(_resp(200), context="list_projects")


def test_401_to_tool_error():
    with pytest.raises(ToolError, match="invalid or expired"):
        raise_for_status(_resp(401), context="list_projects")


def test_403_to_tool_error():
    with pytest.raises(ToolError, match="lacks access"):
        raise_for_status(_resp(403), context="search_documents")


def test_404_to_tool_error_includes_context():
    with pytest.raises(ToolError, match="get_document_metadata"):
        raise_for_status(_resp(404), context="get_document_metadata")


def test_400_passes_through_es_body():
    with pytest.raises(ToolError, match="parsing_exception"):
        raise_for_status(
            _resp(400, json={"error": {"type": "parsing_exception", "reason": "bad query"}}),
            context="search_documents",
        )


def test_5xx_includes_truncated_body():
    long_body = "X" * 2000
    with pytest.raises(ToolError) as exc:
        raise_for_status(_resp(503, body=long_body), context="search_documents")
    assert len(str(exc.value)) < 1000
    assert "503" in str(exc.value)


def test_resource_mode_raises_resource_error():
    with pytest.raises(ResourceError):
        raise_for_status(_resp(404), context="datashare://projects", resource=True)
