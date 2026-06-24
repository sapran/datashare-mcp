import httpx
import pytest

pytestmark = pytest.mark.asyncio


async def test_sends_bearer_header(client, respx_mock):
    route = respx_mock.get("/api/project/").mock(
        return_value=httpx.Response(200, json=[{"name": "leaks"}])
    )
    await client.list_projects()
    assert route.called
    sent = route.calls.last.request.headers["authorization"]
    assert sent == "Bearer test_key"


async def test_list_projects(client, respx_mock):
    respx_mock.get("/api/project/").mock(
        return_value=httpx.Response(200, json=[{"name": "leaks"}, {"name": "panama"}])
    )
    out = await client.list_projects()
    assert [p["name"] for p in out] == ["leaks", "panama"]


async def test_search_passes_through_body(client, respx_mock):
    body_seen = {}

    def handler(request):
        import json

        body_seen.update(json.loads(request.content))
        return httpx.Response(200, json={"hits": {"total": {"value": 0}, "hits": []}})

    respx_mock.post("/api/index/search/leaks/_search").mock(side_effect=handler)
    query = {"query": {"match_all": {}}, "size": 5}
    out = await client.search(project="leaks", query=query)
    assert body_seen == query
    assert out["hits"]["total"]["value"] == 0


async def test_get_document_metadata(client, respx_mock):
    respx_mock.get("/api/leaks/documents/abc").mock(
        return_value=httpx.Response(200, json={"id": "abc", "contentType": "application/pdf"})
    )
    out = await client.get_document_metadata(project="leaks", doc_id="abc")
    assert out["id"] == "abc"


async def test_get_document_metadata_with_routing(client, respx_mock):
    route = respx_mock.get("/api/leaks/documents/abc", params={"routing": "root"}).mock(
        return_value=httpx.Response(200, json={"id": "abc"})
    )
    await client.get_document_metadata(project="leaks", doc_id="abc", routing="root")
    assert route.called


async def test_get_document_content_no_range_uses_es_path(client, respx_mock):
    # With no offset/limit, datashare's no-range endpoint reads the full text from
    # the (often empty) relational DB and 500s. The client must instead probe with
    # limit=0 to learn maxOffset, then fetch offset=0..maxOffset via Elasticsearch.
    seen = []

    def handler(request):
        offset = request.url.params.get("offset")
        limit = request.url.params.get("limit")
        seen.append((offset, limit))
        if limit == "0":
            return httpx.Response(
                200, json={"content": "", "maxOffset": 5, "offset": 0, "limit": 0}
            )
        return httpx.Response(
            200,
            json={"content": "hello", "maxOffset": 5, "offset": 0, "limit": int(limit)},
        )

    respx_mock.get("/api/leaks/documents/content/abc").mock(side_effect=handler)
    out = await client.get_document_content(project="leaks", doc_id="abc")
    assert out["content"] == "hello"
    assert out["maxOffset"] == 5
    # probe (limit=0) then full (limit=maxOffset); never the broken no-range request
    assert seen == [("0", "0"), ("0", "5")]


async def test_get_document_content_full_empty_skips_second_call(client, respx_mock):
    route = respx_mock.get(
        "/api/leaks/documents/content/abc", params={"offset": "0", "limit": "0"}
    ).mock(
        return_value=httpx.Response(
            200, json={"content": "", "maxOffset": 0, "offset": 0, "limit": 0}
        )
    )
    out = await client.get_document_content(project="leaks", doc_id="abc")
    assert out["content"] == ""
    assert out["maxOffset"] == 0
    assert route.call_count == 1  # empty doc → only the probe, no full fetch


async def test_get_document_content_with_range(client, respx_mock):
    route = respx_mock.get(
        "/api/leaks/documents/content/abc",
        params={"offset": "0", "limit": "100"},
    ).mock(
        return_value=httpx.Response(
            200, json={"content": "h", "maxOffset": 5, "start": 0, "size": 1}
        )
    )
    await client.get_document_content(project="leaks", doc_id="abc", offset=0, limit=100)
    assert route.called


async def test_get_document_content_rejects_half_range(client):
    with pytest.raises(ValueError, match="together"):
        await client.get_document_content(project="leaks", doc_id="abc", offset=0)


async def test_get_mapping(client, respx_mock):
    respx_mock.get("/api/index/search/leaks/_mapping").mock(
        return_value=httpx.Response(200, json={"leaks": {"mappings": {"properties": {}}}})
    )
    out = await client.get_mapping(project="leaks")
    assert "leaks" in out


async def test_get_document_content_resource_mode_404(client, respx_mock):
    from fastmcp.exceptions import ResourceError

    respx_mock.get("/api/leaks/documents/content/abc").mock(return_value=httpx.Response(404))
    with pytest.raises(ResourceError):
        await client.get_document_content(project="leaks", doc_id="abc", resource=True)


async def test_401_propagates_as_tool_error(client, respx_mock):
    from fastmcp.exceptions import ToolError

    respx_mock.get("/api/project/").mock(return_value=httpx.Response(401))
    with pytest.raises(ToolError):
        await client.list_projects()


@pytest.mark.parametrize(
    "bad",
    [
        "../etc",
        "leaks/../other",
        "leaks?x=1",
        "leaks#frag",
        "leaks%2F..",
        "",
        "leaks/",
        "leaks ",
    ],
)
async def test_rejects_unsafe_project_segments(client, bad):
    with pytest.raises(ValueError, match="invalid project"):
        await client.search(project=bad, query={})


async def test_rejects_unsafe_doc_id(client):
    with pytest.raises(ValueError, match="invalid doc_id"):
        await client.get_document_metadata(project="leaks", doc_id="../secret")
