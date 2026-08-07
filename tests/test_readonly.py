import httpx
import pytest
import respx

from datashare_mcp.client import DatashareClient
from datashare_mcp.readonly import _SEGMENT, ReadOnlyViolation, is_read_only, read_only_hook

# -- the allowed set ----------------------------------------------------------


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("GET", "/api/project"),
        ("POST", "/api/index/search/tenderchad/_search"),
        ("GET", "/api/index/search/tenderchad/_mapping"),
        ("GET", "/api/tenderchad/documents/abc123"),
        ("GET", "/api/tenderchad/documents/content/abc123"),
    ],
)
def test_client_endpoints_are_allowed(method: str, path: str) -> None:
    assert is_read_only(method, path)


def test_trailing_slash_is_allowed() -> None:
    """The client actually calls `/api/project/`, with the slash."""
    assert is_read_only("GET", "/api/project/")


def test_document_id_charset_is_accepted_in_full() -> None:
    assert is_read_only("GET", "/api/tenderchad/documents/a.b_c-1")


# -- the refused set ----------------------------------------------------------


@pytest.mark.parametrize(
    ("method", "path"),
    [
        # IndexResource administration routes sharing the /api/index prefix.
        ("PUT", "/api/index/tenderchad"),
        ("POST", "/api/index/tenderchad/_close"),
        ("POST", "/api/index/tenderchad/_open"),
        ("PUT", "/api/index/_snapshot/backup"),
        ("DELETE", "/api/index/_snapshot/backup"),
        ("DELETE", "/api/index/_snapshot/backup/snap1"),
        ("POST", "/api/index/_snapshot/backup/snap1/_restore"),
        # Elasticsearch writes through the search proxy.
        ("POST", "/api/index/search/tenderchad/_delete_by_query"),
        ("POST", "/api/index/search/tenderchad/_update_by_query"),
        ("POST", "/api/index/search/tenderchad/_bulk"),
        # GET is forwarded to any ES path by Datashare, so the path pin does the work.
        ("GET", "/api/index/search/tenderchad/_cluster/settings"),
        ("GET", "/api/index/search/tenderchad/_settings"),
        # Document and project writes.
        ("DELETE", "/api/tenderchad/documents/d1"),
        ("POST", "/api/project/"),
        ("DELETE", "/api/project/tenderchad"),
    ],
)
def test_write_requests_are_blocked(method: str, path: str) -> None:
    assert not is_read_only(method, path)


def test_method_alone_refuses_an_allowlisted_path() -> None:
    """`/api/{project}/documents/{doc_id}` is allowlisted for GET and is a live DELETE
    route upstream on the identical path. Only the missing (DELETE, ...) pair refuses it,
    so this fails the moment someone drops the method from a pair."""
    path = "/api/tenderchad/documents/d1"
    assert is_read_only("GET", path)
    for method in ("DELETE", "POST", "PUT", "PATCH"):
        assert not is_read_only(method, path)


def test_segment_cannot_widen_the_path() -> None:
    """`_SEGMENT` excludes `/`, which is what keeps the two document rules distinct and
    stops a segment from absorbing extra path elements."""
    assert not is_read_only("GET", "/api/tenderchad/documents/content/d1/raw")
    assert not is_read_only("POST", "/api/index/search/a/b/_search")
    assert not is_read_only("GET", "/api/project/tenderchad")


# -- end to end through the real client ---------------------------------------


async def test_direct_write_through_the_client_never_reaches_the_wire(
    client: DatashareClient, respx_mock: respx.MockRouter
) -> None:
    route = respx_mock.post("/api/index/tenderchad/_close").mock(
        return_value=httpx.Response(200, json={})
    )
    with pytest.raises(ReadOnlyViolation, match="read-only"):
        await client._http.post("/api/index/tenderchad/_close")
    assert route.call_count == 0


async def test_delete_by_query_never_reaches_the_wire(
    client: DatashareClient, respx_mock: respx.MockRouter
) -> None:
    route = respx_mock.post("/api/index/search/tenderchad/_delete_by_query").mock(
        return_value=httpx.Response(200, json={})
    )
    with pytest.raises(ReadOnlyViolation):
        await client._http.post(
            "/api/index/search/tenderchad/_delete_by_query",
            json={"query": {"match_all": {}}},
        )
    assert route.call_count == 0


async def test_legitimate_reads_still_pass_the_guard(
    client: DatashareClient, respx_mock: respx.MockRouter
) -> None:
    """The guard must not block the endpoints the client genuinely uses."""
    respx_mock.get("/api/project/").mock(return_value=httpx.Response(200, json=[]))
    assert await client.list_projects() == []


# -- redirects ----------------------------------------------------------------
#
# Two independent defences, tested separately.
#
# The first is that this client does not follow redirects at all: `follow_redirects`
# is left at httpx's default of False, so a 3xx surfaces as an error and no request is
# ever replayed. That is the behaviour in force today.
#
# The second is the hook itself, which runs on every request the transport sends. It is
# latent while redirects are not followed, and it is what keeps the guarantee intact if
# redirect-following is ever switched on. It is therefore tested against the hook
# directly rather than through the client.


def test_client_does_not_follow_redirects(client: DatashareClient) -> None:
    assert client._http.follow_redirects is False


async def test_redirect_into_a_write_endpoint_is_never_replayed(
    client: DatashareClient, respx_mock: respx.MockRouter
) -> None:
    respx_mock.get("/api/index/search/tenderchad/_mapping").mock(
        return_value=httpx.Response(
            307, headers={"Location": "http://datashare.test/api/index/tenderchad/_close"}
        )
    )
    blocked = respx_mock.post("/api/index/tenderchad/_close").mock(
        return_value=httpx.Response(200, json={})
    )
    with pytest.raises(Exception, match="307"):
        await client.get_mapping(project="tenderchad")
    assert blocked.call_count == 0


async def test_cross_host_redirect_never_reaches_the_foreign_host(
    client: DatashareClient, respx_mock: respx.MockRouter
) -> None:
    """A 307 preserves method and body, and the client attaches the bearer key to every
    request. The foreign host is registered on the same router, so this fails if the hop
    is ever taken."""
    respx_mock.get("/api/project/").mock(
        return_value=httpx.Response(307, headers={"Location": "http://evil.example/api/project/"})
    )
    foreign = respx.route(host="evil.example").mock(return_value=httpx.Response(200, json=[]))
    respx_mock.routes.add(foreign)

    with pytest.raises(Exception, match="307"):
        await client.list_projects()
    assert foreign.call_count == 0


async def test_hook_refuses_a_hop_into_a_write_endpoint() -> None:
    """The guarantee that survives enabling follow_redirects."""
    hook = read_only_hook("http://datashare.test")
    with pytest.raises(ReadOnlyViolation, match="read-only"):
        await hook(httpx.Request("POST", "http://datashare.test/api/index/tenderchad/_close"))


async def test_request_leaving_the_configured_host_is_blocked() -> None:
    hook = read_only_hook("http://datashare.test")
    with pytest.raises(ReadOnlyViolation, match="configured Datashare host"):
        await hook(httpx.Request("GET", "http://evil.example/api/project/"))


# -- base path handling -------------------------------------------------------


async def test_base_path_prefix_is_stripped_before_matching() -> None:
    hook = read_only_hook("http://ds.test/datashare")
    await hook(httpx.Request("GET", "http://ds.test/datashare/api/project/"))


async def test_request_escaping_the_base_path_is_blocked() -> None:
    hook = read_only_hook("http://ds.test/datashare")
    with pytest.raises(ReadOnlyViolation, match="configured Datashare base path"):
        await hook(httpx.Request("GET", "http://ds.test/api/project/"))


async def test_empty_path_is_normalised_to_root() -> None:
    """A request to the prefix itself leaves an empty path; it must be refused, not
    matched as a bare pattern."""
    hook = read_only_hook("http://ds.test/datashare")
    with pytest.raises(ReadOnlyViolation, match="read-only"):
        await hook(httpx.Request("GET", "http://ds.test/datashare"))


# -- encoding -----------------------------------------------------------------


@pytest.mark.parametrize(
    "path",
    [
        "/api/index/search/tenderchad%2F_close",
        "/api/tenderchad/documents/..%2F..%2Fapi%2Findex%2Ftenderchad",
        "/api/project%2F../index/tenderchad",
    ],
)
async def test_encoded_traversal_cannot_reach_a_write(path: str) -> None:
    hook = read_only_hook("http://datashare.test")
    with pytest.raises(ReadOnlyViolation):
        await hook(httpx.Request("GET", f"http://datashare.test{path}"))


def test_segment_charset_excludes_percent() -> None:
    """Tripwire for matching the decoded `request.url.path`. Decoding can only add
    separators, which makes `fullmatch` stricter — but only while `%` cannot appear
    inside a segment. If `%` is ever admitted, the guard must move to `raw_path`."""
    assert "%" not in _SEGMENT


# -- allowlist shape ----------------------------------------------------------


def test_allowlist_carries_no_mutating_verb() -> None:
    """The tuple is the only thing that can widen the surface, so its shape is contract."""
    from datashare_mcp.readonly import _ALLOWED

    assert {method for method, _ in _ALLOWED} == {"GET", "POST"}
    posts = [pattern.pattern for method, pattern in _ALLOWED if method == "POST"]
    assert posts == [rf"/api/index/search/{_SEGMENT}/_search/?"]
    assert len(_ALLOWED) == 5
