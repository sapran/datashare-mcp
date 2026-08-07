from __future__ import annotations

import re
from collections.abc import Awaitable, Callable

import httpx

# Every request this server may issue, as (method, path) pairs relative to the Datashare
# root. Enforced on every outgoing httpx request, redirect hops included, so a mutating
# call cannot reach Datashare even when the API key or the instance's mode would permit
# it. Extending this tuple is the only way to widen the surface; no tool argument,
# caller value or redirect can.
#
# The threat is specific and was read from ICIJ/datashare at tag 21.2.1, in
# datashare-app/src/main/java/org/icij/datashare/web/IndexResource.java and
# datashare-app/src/main/java/org/icij/datashare/utils/IndexAccessVerifier.java:
#
#   1. IndexResource is @Prefix("/api/index") — the same prefix as the two endpoints
#      below that this server legitimately calls. It also registers, on that prefix:
#        PUT    /api/index/:index                                (createIndex)
#        POST   /api/index/:index/_close                         (takes the corpus offline)
#        POST   /api/index/:index/_open
#        PUT    /api/index/_snapshot/:repository
#        DELETE /api/index/_snapshot/:repository
#        DELETE /api/index/_snapshot/:repository/:snapshot
#        POST   /api/index/_snapshot/:repository/:snapshot/_restore
#      Every one of those except createIndex is guarded only by
#      `modeVerifier.checkAllowedMode(Mode.LOCAL, Mode.EMBEDDED)` — which admits the
#      LOCAL mode this server is deployed against. createIndex carries no mode check at
#      all. Nothing else protects them. The path pin below is the whole of the defence.
#
#   2. @Post("/search/:path:") is a greedy raw Elasticsearch proxy; its own javadoc says
#      "everything sent is forwarded to Elasticsearch". IndexAccessVerifier.checkPath
#      narrows POST via `areAllIndexesGranted && (isMethodGet || isSearchPath ||
#      isCountPath)`, so _delete_by_query is refused server-side *today* — but that is a
#      property of this Datashare version and mode, not a promise to this client. Pinning
#      POST to the literal _search suffix makes the guarantee ours.
#
#   3. In that same expression `isMethodGet` is an unconditional OR branch: under GET,
#      *any* Elasticsearch path is forwarded (the upstream comment reads "As it is a GET
#      method, all paths are accepted"). Pinning GET to the literal _mapping suffix is
#      therefore load-bearing, not decorative.
#
# LOCAL mode does not enforce API-key authentication either (only LocalUserFilter), so
# "the key is read-only" is not an available defence and this allowlist is the only one.
#
# _SEGMENT deliberately reuses the charset of _SAFE_PATH_SEGMENT in client.py so the
# input validator and this guard cannot disagree. Excluding `/` is what keeps
# /api/{project}/documents/{doc_id} from also swallowing
# /api/{project}/documents/content/{doc_id}.
_SEGMENT = r"[A-Za-z0-9._-]+"

_ALLOWED: tuple[tuple[str, re.Pattern[str]], ...] = tuple(
    (method, re.compile(rf"{path}/?"))
    for method, path in (
        ("GET", r"/api/project"),
        ("POST", rf"/api/index/search/{_SEGMENT}/_search"),
        ("GET", rf"/api/index/search/{_SEGMENT}/_mapping"),
        ("GET", rf"/api/{_SEGMENT}/documents/{_SEGMENT}"),
        ("GET", rf"/api/{_SEGMENT}/documents/content/{_SEGMENT}"),
    )
)


class ReadOnlyViolation(RuntimeError):
    """A request outside the read-only allowlist was attempted and refused."""


def is_read_only(method: str, path: str) -> bool:
    """True when (method, path) is one of the Datashare read endpoints this server may call.

    `path` is relative to the Datashare root, i.e. with any base-URL prefix already
    removed. Never drop the method from a pair: `/api/{project}/documents/{doc_id}` is a
    live DELETE route upstream on the identical path, and only the absence of a
    `(DELETE, ...)` pair refuses it.
    """
    return any(m == method and p.fullmatch(path) for m, p in _ALLOWED)


def read_only_hook(url: str) -> Callable[[httpx.Request], Awaitable[None]]:
    """Build the httpx request hook that pins every request to `url`'s host and the allowlist.

    The returned hook runs for every request the client sends, redirect hops included, so
    the guarantee holds regardless of what the API key or the instance's mode permits
    server-side. It refuses a request that leaves the configured host — a 307 preserves
    method and body, and this client attaches the bearer key to every request, so a
    redirect would otherwise replay both somewhere the operator never configured — and it
    strips the configured base path before matching, so a Datashare mounted under
    https://example.org/datashare is checked on /api/... like any other.

    Matching uses the decoded `request.url.path`. That is safe only because `%` is outside
    the `_SEGMENT` charset: decoding can add separators, which makes `fullmatch` stricter,
    never looser. `test_segment_charset_excludes_percent` is the tripwire — if `%` is ever
    admitted, this must move to `raw_path`.
    """
    base = httpx.URL(url)
    expected_host = base.host
    prefix = base.path.rstrip("/")

    async def enforce_read_only(request: httpx.Request) -> None:
        if request.url.host != expected_host:
            raise ReadOnlyViolation(
                f"blocked {request.method} {request.url}: this request leaves the "
                f"configured Datashare host {expected_host}"
            )
        path = request.url.path
        if prefix:
            if path == prefix:
                path = "/"
            elif path.startswith(f"{prefix}/"):
                path = path[len(prefix) :]
            else:
                raise ReadOnlyViolation(
                    f"blocked {request.method} {request.url}: this request leaves the "
                    f"configured Datashare base path {prefix}"
                )
        if not is_read_only(request.method, path):
            raise ReadOnlyViolation(
                f"blocked {request.method} {request.url}: datashare-mcp is read-only and "
                "only calls a fixed allowlist of Datashare read endpoints"
            )

    return enforce_read_only
