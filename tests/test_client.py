import asyncio

import httpx
import pytest

from datashare_mcp.client import DatashareClient
from datashare_mcp.config import Settings
from tests.shapes import (
    assert_content_payload,
    assert_project_list,
    assert_search_envelope,
    content_payload,
    date_range_aggs,
    document_metadata,
    language_buckets,
    project_list,
    search_payload,
    type_buckets,
    year_buckets,
)

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
        return_value=httpx.Response(200, json=project_list("leaks", "panama"))
    )
    out = await client.list_projects()
    assert_project_list(out)
    assert [p["name"] for p in out] == ["leaks", "panama"]


async def test_search_passes_through_body(client, respx_mock):
    body_seen = {}

    def handler(request):
        import json

        body_seen.update(json.loads(request.content))
        return httpx.Response(200, json=search_payload())

    respx_mock.post("/api/index/search/leaks/_search").mock(side_effect=handler)
    query = {"query": {"match_all": {}}, "size": 5}
    out = await client.search(project="leaks", query=query)
    assert body_seen == query
    assert_search_envelope(out, min_hits=1)
    assert out["hits"]["hits"][0]["_routing"] == "d1"


@pytest.mark.parametrize(
    "query",
    [
        pytest.param({"script_fields": {"x": {"script": "1"}}}, id="script_fields"),
        pytest.param({"runtime_mappings": {"x": {"type": "long"}}}, id="runtime_mappings"),
        pytest.param(
            {"query": {"bool": {"must": [{"script": {"script": "true"}}]}}},
            id="script-nested-in-bool",
        ),
        pytest.param(
            {"query": {"function_score": {"functions": [{"script_score": {}}]}}},
            id="script_score-nested-in-list",
        ),
        pytest.param(
            {"query": {"terms": {"id": {"index": "other", "id": "1", "path": "ids"}}}},
            id="terms-lookup-reads-another-index",
        ),
        pytest.param(
            {
                "aggs": {
                    "m": {
                        "scripted_metric": {
                            "init_script": "state.x = []",
                            "map_script": "state.x.add(1)",
                            "combine_script": "return state.x",
                            "reduce_script": "return states",
                        }
                    }
                }
            },
            id="scripted_metric-walked-through-the-old-four-key-denylist",
        ),
        pytest.param(
            {"aggs": {"m": {"avg": {"script": {"source": "doc['n'].value"}}}}},
            id="script-inside-a-metric-aggregation",
        ),
        pytest.param(
            {"sort": [{"_script": {"type": "number", "script": "1"}}]},
            id="_script-sort",
        ),
        pytest.param(
            {"query": {"bool": {"filter": [{"script_score": {"script": {"id": "stored"}}}]}}},
            id="stored-script-by-id",
        ),
    ],
)
async def test_search_refuses_dangerous_bodies(client, respx_mock, query):
    """The path allowlist pins the URL; these escape it through the body instead.

    The route must stay uncalled: refusal happens before the request is issued, so a
    dangerous body never reaches Datashare's raw Elasticsearch proxy at all.
    """
    route = respx_mock.post("/api/index/search/leaks/_search").mock(
        return_value=httpx.Response(200, json=search_payload())
    )
    with pytest.raises(ValueError, match="invalid query"):
        await client.search(project="leaks", query=query)
    assert not route.called


async def test_search_allows_a_plain_terms_filter_and_terms_aggregation(client, respx_mock):
    """The terms-lookup check must not swallow the ordinary uses of the same keyword."""
    body_seen = {}

    def handler(request):
        import json

        body_seen.update(json.loads(request.content))
        return httpx.Response(200, json=search_payload())

    respx_mock.post("/api/index/search/leaks/_search").mock(side_effect=handler)
    query = {
        "query": {"terms": {"tags": ["a", "b"]}},
        "aggs": {"by_type": {"terms": {"field": "contentType", "size": 100}}},
        "size": 0,
    }
    await client.search(project="leaks", query=query)
    assert body_seen == query


async def test_search_clamps_size_but_refuses_an_over_large_from(client, respx_mock, settings):
    """`size` is a page size — clamping returns fewer hits and `hits.total` still tells
    the truth. `from` is a cursor — clamping it silently answers a different question."""
    body_seen = {}

    def handler(request):
        import json

        body_seen.update(json.loads(request.content))
        return httpx.Response(200, json=search_payload())

    respx_mock.post("/api/index/search/leaks/_search").mock(side_effect=handler)
    query = {"query": {"match_all": {}}, "size": 10000}
    await client.search(project="leaks", query=query)
    assert body_seen["size"] == settings.max_search_size
    assert query["size"] == 10000, "the caller's dict must not be mutated"

    with pytest.raises(ValueError, match="exceeds the configured maximum"):
        await client.search(project="leaks", query={"from": 99999})


@pytest.mark.parametrize(
    "query",
    [
        pytest.param({"query": {"term": {"from": "alice@example.com"}}}, id="from-is-a-field"),
        pytest.param(
            {"query": {"range": {"creationDate": {"from": "2020-01-01", "to": "2021-01-01"}}}},
            id="from-is-a-range-bound",
        ),
        pytest.param({"query": {"match": {"description": "kremlin"}}}, id="description-field"),
        pytest.param(
            {"query": {"term": {"tika_metadata_dc_description": "x"}}}, id="tika-description"
        ),
    ],
)
async def test_ordinary_corpus_queries_are_not_refused(client, respx_mock, query):
    """`size`/`from` are pagination only at the top level, and the script rule matches a
    token, not the letters: an email corpus searches a `from` field, and `description`
    merely contains \"script\"."""
    body_seen = {}

    def handler(request):
        import json

        body_seen.update(json.loads(request.content))
        return httpx.Response(200, json=search_payload())

    respx_mock.post("/api/index/search/leaks/_search").mock(side_effect=handler)
    await client.search(project="leaks", query=query)
    assert body_seen == query, "the body must reach Datashare unaltered"


async def test_aggregation_size_is_clamped(client, respx_mock, settings):
    """Inside `aggs`, `size` is always a cardinality — and `_id` fielddata is enabled on
    the shipped stack, so an unbounded terms agg is the cheap way to exhaust the heap."""
    body_seen = {}

    def handler(request):
        import json

        body_seen.update(json.loads(request.content))
        return httpx.Response(200, json=search_payload())

    respx_mock.post("/api/index/search/leaks/_search").mock(side_effect=handler)
    await client.search(
        project="leaks", query={"aggs": {"a": {"terms": {"field": "_id", "size": 100000}}}}
    )
    assert body_seen["aggs"]["a"]["terms"]["size"] == settings.max_search_size


async def test_search_rejects_a_non_object_body(client, respx_mock):
    route = respx_mock.post("/api/index/search/leaks/_search").mock(
        return_value=httpx.Response(200, json=search_payload())
    )
    with pytest.raises(ValueError, match="must be an object"):
        await client.search(project="leaks", query=["not", "a", "body"])
    assert not route.called


async def test_get_document_metadata(client, respx_mock):
    respx_mock.get("/api/leaks/documents/abc").mock(
        return_value=httpx.Response(200, json=document_metadata(id="abc"))
    )
    out = await client.get_document_metadata(project="leaks", doc_id="abc")
    assert out["id"] == "abc"
    assert out["contentType"] == "application/pdf"


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
            return httpx.Response(200, json=content_payload(content="", max_offset=5, limit=0))
        return httpx.Response(
            200, json=content_payload(content="hello", max_offset=5, limit=int(limit))
        )

    respx_mock.get("/api/leaks/documents/content/abc").mock(side_effect=handler)
    out = await client.get_document_content(project="leaks", doc_id="abc")
    assert_content_payload(out, whole_document=True)
    assert out["content"] == "hello"
    assert out["maxOffset"] == 5
    # probe (limit=0) then full (limit=maxOffset); never the broken no-range request
    assert seen == [("0", "0"), ("0", "5")]


async def test_get_document_content_full_empty_skips_second_call(client, respx_mock):
    route = respx_mock.get(
        "/api/leaks/documents/content/abc", params={"offset": "0", "limit": "0"}
    ).mock(return_value=httpx.Response(200, json=content_payload(content="", max_offset=0)))
    out = await client.get_document_content(project="leaks", doc_id="abc")
    assert_content_payload(out, whole_document=True)
    assert out["content"] == ""
    assert out["maxOffset"] == 0
    assert route.call_count == 1  # empty doc → only the probe, no full fetch


async def test_get_document_content_with_range(client, respx_mock):
    route = respx_mock.get(
        "/api/leaks/documents/content/abc",
        params={"offset": "0", "limit": "100"},
    ).mock(
        return_value=httpx.Response(200, json=content_payload(content="h", max_offset=5, limit=100))
    )
    out = await client.get_document_content(project="leaks", doc_id="abc", offset=0, limit=100)
    assert route.called
    # A ranged read returns a prefix: maxOffset is still the full length.
    assert_content_payload(out)
    assert len(out["content"]) < out["maxOffset"]


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


async def test_get_project_overview(client, respx_mock):
    respx_mock.post("/api/index/search/leaks/_search").mock(
        return_value=httpx.Response(
            200,
            json=search_payload(
                hits=[],
                total=100,
                aggregations=language_buckets(("ENGLISH", 80), ("RUSSIAN", 20))
                | date_range_aggs(min_ms=1000000, max_ms=2000000),
            ),
        )
    )
    out = await client.get_project_overview(project="leaks")
    assert out["totalDocuments"] == 100
    assert len(out["languageDistribution"]) == 2
    assert out["dateRange"]["min"] == 1000000


async def test_get_document_type_distribution(client, respx_mock):
    def handler(request):
        import json

        body = json.loads(request.content)
        if "aggs" in body and "by_type" in body["aggs"]:
            return httpx.Response(
                200,
                json=search_payload(
                    hits=[],
                    total=100,
                    aggregations=type_buckets(
                        ("application/pdf", 50),
                        ("application/msword", 30),
                        (
                            "application/vnd.openxmlformats-officedocument."
                            "wordprocessingml.document",
                            20,
                        ),
                    ),
                ),
            )
        return httpx.Response(200, json=search_payload(hits=[], total=100))

    respx_mock.post("/api/index/search/leaks/_search").mock(side_effect=handler)
    out = await client.get_document_type_distribution(project="leaks")
    assert out["totalDocuments"] == 100
    type_dict = {t["type"]: t for t in out["typeDistribution"]}
    assert type_dict["PDF Documents"]["count"] == 50
    assert type_dict["Word Documents"]["count"] == 50
    assert type_dict["Word Documents"]["percentage"] == 50.0


async def test_get_temporal_distribution(client, respx_mock):
    respx_mock.post("/api/index/search/leaks/_search").mock(
        return_value=httpx.Response(
            200,
            json=search_payload(
                hits=[], total=100, aggregations=year_buckets((2021, 30), (2022, 70))
            ),
        )
    )
    out = await client.get_temporal_distribution(project="leaks")
    assert out["totalDocuments"] == 100
    assert len(out["yearlyDistribution"]) == 2
    years = [y["year"] for y in out["yearlyDistribution"]]
    assert 2021 in years
    assert 2022 in years


async def test_get_project_summary_json(client, respx_mock):
    respx_mock.post("/api/index/search/leaks/_search").mock(
        return_value=httpx.Response(
            200,
            json=search_payload(
                hits=[],
                total=100,
                aggregations=language_buckets(("ENGLISH", 80))
                | date_range_aggs(min_ms=1000000, max_ms=2000000)
                | type_buckets(("application/pdf", 50))
                | year_buckets((2021, 30)),
            ),
        )
    )
    respx_mock.get("/api/leaks/documents/content/abc").mock(
        return_value=httpx.Response(200, json=content_payload(content="test"))
    )
    out = await client.get_project_summary(project="leaks", format="json")
    assert "metadata" in out
    assert "overview" in out
    assert out["overview"]["totalDocuments"] == 100


async def test_get_project_summary_markdown(client, respx_mock):
    respx_mock.post("/api/index/search/leaks/_search").mock(
        return_value=httpx.Response(
            200,
            json=search_payload(
                hits=[],
                total=100,
                aggregations=language_buckets(("ENGLISH", 80))
                | date_range_aggs(min_ms=1609459200000, max_ms=1640995200000)
                | type_buckets(("application/pdf", 50))
                | year_buckets((2021, 30), (2022, 70)),
            ),
        )
    )
    respx_mock.get("/api/leaks/documents/content/abc").mock(
        return_value=httpx.Response(200, json=content_payload(content="test"))
    )

    out = await client.get_project_summary(project="leaks", format="markdown")
    assert "markdown" in out
    assert "# Project Summary" in out["markdown"]
    assert "ENGLISH" in out["markdown"]
    assert "2021" in out["markdown"]


# -- bounded content fetches --------------------------------------------------


async def test_full_content_fetch_is_capped_at_max_content_bytes(client, respx_mock, settings):
    """`maxOffset` is a remote value derived from a document this server did not author.
    It used to become the `limit` verbatim, so one call could ask for the whole text of
    an arbitrarily large planted document and buffer it in this process."""
    requested = []

    def handler(request):
        requested.append(int(request.url.params["limit"]))
        huge = 50 * settings.max_content_bytes
        if requested[-1] == 0:
            return httpx.Response(200, json=content_payload(content="", max_offset=huge, limit=0))
        return httpx.Response(
            200,
            json=content_payload(content="x" * requested[-1], max_offset=huge, limit=requested[-1]),
        )

    respx_mock.get("/api/leaks/documents/content/abc").mock(side_effect=handler)
    out = await client.get_document_content(project="leaks", doc_id="abc")

    assert requested == [0, settings.max_content_bytes]
    assert out["truncated"] is True
    assert len(out["content"]) == settings.max_content_bytes


async def test_explicit_limit_is_capped_too(client, respx_mock, settings):
    requested = []

    def handler(request):
        requested.append(int(request.url.params["limit"]))
        return httpx.Response(200, json=content_payload(content="x" * requested[-1]))

    respx_mock.get("/api/leaks/documents/content/abc").mock(side_effect=handler)
    out = await client.get_document_content(
        project="leaks", doc_id="abc", offset=0, limit=99_000_000
    )
    assert requested == [settings.max_content_bytes]
    assert out["truncated"] is True


async def test_small_document_is_returned_whole_and_unmarked(client, respx_mock):
    """The cap must not change the ordinary case, nor add a truncation marker to it."""

    def handler(request):
        if int(request.url.params["limit"]) == 0:
            return httpx.Response(200, json=content_payload(content="", max_offset=5, limit=0))
        return httpx.Response(200, json=content_payload(content="hello", max_offset=5))

    respx_mock.get("/api/leaks/documents/content/abc").mock(side_effect=handler)
    out = await client.get_document_content(project="leaks", doc_id="abc")
    assert_content_payload(out, whole_document=True)
    assert "truncated" not in out


@pytest.mark.parametrize("bad", ["not-a-number", None, True, {"n": 1}, [1]])
async def test_unusable_max_offset_returns_the_probe_instead_of_raising(
    client, respx_mock, bad
):
    """`probe.get("maxOffset", 0) or 0` kept any truthy non-integer, and the `<= 0`
    comparison after it raised TypeError, which no tool handler caught."""
    payload = content_payload(content="", limit=0)
    payload["maxOffset"] = bad
    respx_mock.get("/api/leaks/documents/content/abc").mock(
        return_value=httpx.Response(200, json=payload)
    )
    out = await client.get_document_content(project="leaks", doc_id="abc")
    assert out["content"] == ""


async def test_oversized_response_is_refused_before_it_is_decoded(client, respx_mock, settings):
    """The request asks for at most the cap; nothing obliges the remote side to obey."""
    oversized = "y" * (settings.max_content_bytes * 5)

    def handler(request):
        if int(request.url.params["limit"]) == 0:
            return httpx.Response(
                200, json=content_payload(content="", max_offset=len(oversized), limit=0)
            )
        return httpx.Response(200, json=content_payload(content=oversized))

    respx_mock.get("/api/leaks/documents/content/abc").mock(side_effect=handler)
    with pytest.raises(ValueError, match=r"exceeds the .* ceiling"):
        await client.get_document_content(project="leaks", doc_id="abc")


# -- corpus-derived values are not trusted arithmetic inputs ------------------


async def test_markdown_summary_survives_an_absurd_creation_date(client, respx_mock):
    """creationDate is extracted from the document file, so an out-of-range epoch reaches
    fromtimestamp intact. Unwrapped it raised, permanently breaking the markdown summary
    for that project — and the raise is not a ValueError in every case, so no tool handler
    caught it."""
    respx_mock.post("/api/index/search/leaks/_search").mock(
        return_value=httpx.Response(
            200,
            json=search_payload(
                hits=[],
                total=100,
                aggregations=language_buckets(("ENGLISH", 80))
                | date_range_aggs(min_ms=10**20, max_ms=10**20)
                | type_buckets(("application/pdf", 50))
                | year_buckets((2021, 30)),
            ),
        )
    )
    respx_mock.get("/api/leaks/documents/content/abc").mock(
        return_value=httpx.Response(200, json=content_payload(content="test"))
    )

    out = await client.get_project_summary(project="leaks", format="markdown")
    assert "# Project Summary" in out["markdown"]
    assert "Date Range:** unknown" in out["markdown"]


async def test_temporal_distribution_survives_a_zero_total_with_buckets(client, respx_mock):
    """The count and the histogram are separate requests, so they can disagree — an index
    being written to is enough. Dividing by the count raised ZeroDivisionError, which is
    not a ValueError and so escaped every tool handler."""
    respx_mock.post("/api/index/search/leaks/_search").mock(
        return_value=httpx.Response(
            200,
            json=search_payload(
                hits=[],
                total=0,
                aggregations=year_buckets((2020, 1), (2021, 1), (2022, 90)),
            ),
        )
    )
    out = await client.get_temporal_distribution(project="leaks")
    assert out["totalDocuments"] == 0
    assert out["peaks"], "a spike is still reported, just without a percentage"
    assert "% of corpus" not in out["peaks"][0]["note"]


async def test_document_structure_survives_hits_without_source(client, respx_mock):
    """`hit["_source"]` was a hard subscript, and `path` a document-supplied value: a hit
    without _source raised KeyError, a non-string path raised TypeError."""
    respx_mock.post("/api/index/search/leaks/_search").mock(
        return_value=httpx.Response(
            200,
            json=search_payload(
                hits=[
                    {"_id": "a", "_routing": "a"},
                    {"_id": "b", "_routing": "b", "_source": {"path": 12345}},
                    {"_id": "c", "_routing": "c", "_source": {"path": "/media/docs/T-1/x.pdf"}},
                ],
                total=3,
            ),
        )
    )
    out = await client._analyze_document_structure("leaks")
    assert out["totalUniqueTenders"] == 1
    assert out["sampleTenderIds"] == ["T-1"]


# -- every response is bounded, in bytes and in wall time ---------------------


async def test_search_response_is_bounded_too(client, respx_mock, settings):
    """The byte ceiling used to guard `get_document_content` alone. `size` is clamped to
    200 hits, but per-hit `_source` length is third-party controlled, so hit count is not
    a byte bound."""
    ceiling = settings.max_content_bytes * 4 + 8192
    respx_mock.post("/api/index/search/leaks/_search").mock(
        return_value=httpx.Response(200, content=b"y" * (ceiling + 1))
    )
    with pytest.raises(ValueError, match=r"exceeds the .* ceiling"):
        await client.search(project="leaks", query={"query": {"match_all": {}}})


@pytest.mark.parametrize(
    ("method", "kwargs"),
    [
        ("list_projects", {}),
        ("get_mapping", {"project": "leaks"}),
        ("get_document_metadata", {"project": "leaks", "doc_id": "abc"}),
    ],
)
async def test_every_endpoint_is_bounded(client, respx_mock, settings, method, kwargs):
    ceiling = settings.max_content_bytes * 4 + 8192
    oversized = httpx.Response(200, content=b"z" * (ceiling + 1))
    respx_mock.get("/api/project/").mock(return_value=oversized)
    respx_mock.get("/api/index/search/leaks/_mapping").mock(return_value=oversized)
    respx_mock.get("/api/leaks/documents/abc").mock(return_value=oversized)
    with pytest.raises(ValueError, match=r"exceeds the .* ceiling"):
        await getattr(client, method)(**kwargs)


async def test_oversized_body_without_content_length_is_abandoned_mid_stream(
    client, respx_mock, settings
):
    """A chunked response declares no length, so the pre-read check cannot see it. The
    running counter must stop it before the whole body is materialised."""
    ceiling = settings.max_content_bytes * 4 + 8192
    chunk = b"q" * 65536
    sent = {"n": 0}

    async def chunks():
        while True:
            sent["n"] += 1
            yield chunk

    respx_mock.get("/api/project/").mock(return_value=httpx.Response(200, content=chunks()))
    with pytest.raises(ValueError, match="mid-stream"):
        await client.list_projects()
    # Stopped near the ceiling rather than reading forever.
    assert sent["n"] * len(chunk) < ceiling + 2 * len(chunk)


async def test_slow_drip_response_hits_the_total_deadline(respx_mock, settings, monkeypatch):
    """httpx's read timeout bounds the wait for the *next chunk*, so a response dripping
    one byte well inside timeout_secs never trips it and streams forever."""
    monkeypatch.setenv("DATASHARE_DEADLINE_SECS", "1")
    slow_settings = Settings()
    slow_client = DatashareClient(slow_settings)

    async def drip():
        for _ in range(1000):
            await asyncio.sleep(0.05)
            yield b" "

    respx_mock.get("/api/project/").mock(return_value=httpx.Response(200, content=drip()))
    try:
        with pytest.raises(ValueError, match="did not finish responding within"):
            await slow_client.list_projects()
    finally:
        await slow_client.aclose()
