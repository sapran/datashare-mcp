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
        ("PUT", "/api/index/_snapshot/backup/snap1"),
        ("DELETE", "/api/index/_snapshot/backup/snap1"),
        ("POST", "/api/index/_snapshot/backup/snap1/_restore"),
        ("HEAD", "/api/index/search/tenderchad/_search"),
        ("OPTIONS", "/api/index/tenderchad"),
        ("OPTIONS", "/api/index/search/tenderchad/_search"),
        # Elasticsearch writes through the search proxy.
        ("POST", "/api/index/search/tenderchad/_delete_by_query"),
        ("POST", "/api/index/search/tenderchad/_update_by_query"),
        ("POST", "/api/index/search/tenderchad/_bulk"),
        # GET is forwarded to any ES path by Datashare, so the path pin does the work.
        ("GET", "/api/index/search/tenderchad/_cluster/settings"),
        ("GET", "/api/index/search/tenderchad/_settings"),
        ("GET", "/api/index/search/_search/scroll"),
        ("POST", "/api/index/search/_search/scroll"),
        # Document and project writes.
        ("DELETE", "/api/tenderchad/documents/d1"),
        ("POST", "/api/project/"),
        ("DELETE", "/api/project/tenderchad"),
        ("PUT", "/api/tenderchad/documents/tags/d1"),
        ("POST", "/api/tenderchad/documents/batchUpdate/star"),
    ],
)
def test_write_requests_are_blocked(method: str, path: str) -> None:
    assert not is_read_only(method, path)


def test_method_alone_refuses_an_allowlisted_path() -> None:
    """`/api/{project}/documents/{doc_id}` is allowlisted for GET. Upstream registers no
    mutating handler on that exact path today, but its namespace carries several
    (`documents/tags/:docId`, `documents/untag/:docId`, `documents/batchUpdate/*` in
    DocumentResource.java at 21.2.1). The method pin is what keeps a mutating verb refused
    if one is ever added on the identical path, so this fails the moment someone drops the
    method from a pair."""
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
#
# The guard matches `raw_path` — the bytes httpx sends — not the percent-decoded
# `request.url.path`. Matching the decoded form authorises one string and sends another,
# because httpx's quote() preserves pre-existing %xx escapes on the wire.


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


@pytest.mark.parametrize(
    ("method", "path"),
    [
        # Decodes to `/api/../documents/d1`, which fullmatches the document rule. The
        # wire carries the encoded form, which does not.
        ("GET", "/api/%2e%2e/documents/d1"),
        # Decodes to `.../_search`; the wire carries `_sea%72ch`.
        ("POST", "/api/index/search/tenderchad/_sea%72ch"),
        # Every character of an allowlisted path, re-encoded.
        ("GET", "/api/%70roject"),
        ("GET", "/api/index/search/tenderchad/%5Fmapping"),
    ],
)
async def test_percent_encoded_path_is_refused_even_when_it_decodes_to_an_allowed_one(
    method: str, path: str
) -> None:
    """The guard must authorise the string that is actually sent.

    This is the regression test for matching `request.url.path`: each of these decodes to
    something the allowlist accepts while the wire target does not, so a decoded-path
    guard lets them through.
    """
    request = httpx.Request(method, f"http://datashare.test{path}")
    assert request.url.raw_path.decode() != request.url.path, "test case does not diverge"
    hook = read_only_hook("http://datashare.test")
    with pytest.raises(ReadOnlyViolation, match="read-only"):
        await hook(request)


def test_segment_charset_excludes_percent() -> None:
    """`_SEGMENT` must not admit `%`, so a legitimate path this client builds is always
    identical raw and decoded — which is what makes matching `raw_path` free of false
    refusals. `client._validate_path_segment` enforces the same charset on caller input."""
    assert "%" not in _SEGMENT


@pytest.mark.parametrize(
    ("scheme", "host", "port"),
    [
        ("http", "datashare.test", 9201),  # Elasticsearch on the same machine
        ("http", "evil.example", None),
        ("https", "datashare.test", None),  # not a downgrade, but a different origin
    ],
)
async def test_origin_pin_covers_scheme_and_port(scheme: str, host: str, port: int | None) -> None:
    """Host alone is not the origin. In the documented local topology port 9201 is the
    Elasticsearch container, a different trust domain, and this client attaches the bearer
    key to every request it sends."""
    hook = read_only_hook("http://datashare.test")
    netloc = host if port is None else f"{host}:{port}"
    with pytest.raises(ReadOnlyViolation, match="configured Datashare host"):
        await hook(httpx.Request("GET", f"{scheme}://{netloc}/api/project/"))


async def test_https_downgrade_to_http_is_refused() -> None:
    hook = read_only_hook("https://ds.example.org")
    with pytest.raises(ReadOnlyViolation, match="configured Datashare host"):
        await hook(httpx.Request("GET", "http://ds.example.org/api/project/"))


async def test_explicit_default_port_still_matches() -> None:
    """httpx normalises the default port to None on both operands, so configuring
    `http://datashare.test:80` must not refuse every request."""
    hook = read_only_hook("http://datashare.test:80")
    await hook(httpx.Request("GET", "http://datashare.test/api/project/"))


# -- allowlist shape ----------------------------------------------------------


def test_allowlist_carries_no_mutating_verb() -> None:
    """The tuple is the only thing that can widen the surface, so its shape is contract."""
    from datashare_mcp.readonly import _ALLOWED

    assert {method for method, _ in _ALLOWED} == {"GET", "POST"}
    posts = [pattern.pattern for method, pattern in _ALLOWED if method == "POST"]
    assert posts == [rf"/api/index/search/{_SEGMENT}/_search/?"]
    assert len(_ALLOWED) == 5


def test_validator_is_built_from_the_guard_charset() -> None:
    """readonly.py claims the input validator and the guard cannot disagree. They cannot,
    because client.py compiles its validator from `_SEGMENT` rather than restating it."""
    from datashare_mcp.client import _SAFE_PATH_SEGMENT

    assert _SAFE_PATH_SEGMENT.pattern == _SEGMENT


@pytest.mark.parametrize("value", ["tenderchad\n", "\ntenderchad", "tender chad", "a/b", "a%2Fb"])
def test_validator_rejects_whitespace_and_separators(value: str) -> None:
    """`re.match` with `^...$` would accept a trailing newline — Python's `$` matches
    before one. The validator uses `fullmatch`, so it does not."""
    from datashare_mcp.client import _validate_path_segment

    with pytest.raises(ValueError, match="invalid project"):
        _validate_path_segment(value, field="project")
