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


def test_400_shows_the_error_type_but_not_the_reason():
    """`type` is a snake_case token; `reason` is free text naming the index, the field
    and often the analyzer chain — and search_documents lets the caller provoke it."""
    with pytest.raises(ToolError) as exc:
        raise_for_status(
            _resp(
                400,
                json={
                    "error": {
                        "type": "parsing_exception",
                        "reason": "failed to parse [content.keyword] on index leaks-2024",
                        "index": "leaks-2024",
                    }
                },
            ),
            context="search_documents",
        )
    message = str(exc.value)
    assert "parsing_exception" in message
    assert "leaks-2024" not in message
    assert "failed to parse" not in message


@pytest.mark.parametrize(
    "kind",
    ["Bad Thing: /etc/passwd", "type with spaces", "UPPER_CASE", "x" * 100, 42, None],
)
def test_400_drops_an_error_type_that_is_not_a_plain_token(kind):
    with pytest.raises(ToolError) as exc:
        raise_for_status(_resp(400, json={"error": {"type": kind}}), context="search_documents")
    message = str(exc.value)
    assert "bad request (400)" in message
    assert str(kind) not in message


def test_400_with_an_unparseable_body_still_reports_the_status():
    with pytest.raises(ToolError, match=r"bad request \(400\)"):
        raise_for_status(_resp(400, body="<html>nginx</html>"), context="search_documents")


def test_5xx_does_not_echo_the_upstream_body():
    """Datashare's no-range content endpoint returns 500 for index-only documents, so this
    is a routine path — and a Java 500 body is the most stack-trace-rich thing it emits."""
    stack = (
        "java.lang.NullPointerException\n"
        "\tat org.icij.datashare.web.DocumentResource.get(DocumentResource.java:118)\n"
        "\tat /home/datashare/app/lib/datashare-app-21.2.1.jar"
    )
    with pytest.raises(ToolError) as exc:
        raise_for_status(_resp(503, body=stack), context="search_documents")
    message = str(exc.value)
    assert "503" in message
    assert "java.lang" not in message
    assert "DocumentResource.java" not in message
    assert "/home/datashare" not in message


def test_upstream_body_is_available_to_the_operator_in_the_debug_log(caplog):
    with caplog.at_level("DEBUG", logger="datashare_mcp"):
        with pytest.raises(ToolError):
            raise_for_status(_resp(500, body="java.lang.NullPointerException"), context="search")
    assert "java.lang.NullPointerException" in caplog.text


def test_resource_mode_raises_resource_error():
    with pytest.raises(ResourceError):
        raise_for_status(_resp(404), context="datashare://projects", resource=True)
