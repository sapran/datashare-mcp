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
# SCOPE — this is an in-client control. It bounds what *this process* sends, and nothing
# else. It is not a boundary around the Datashare instance: in the shipped local stack,
# Datashare runs in LOCAL mode and Elasticsearch with xpack.security.enabled=false, so any
# other process on the host — including an agent that also holds a shell or a generic HTTP
# tool — reaches 127.0.0.1:8888 and 127.0.0.1:9201 unauthenticated, with full read and
# write access, and this allowlist never sees those requests. What the allowlist buys is
# that a compromised or mistaken *caller of this server* cannot turn it into the write
# path. See the README, "What the allowlist is worth, and what it is not".
#
# The threat is specific and was read from ICIJ/datashare at tag 21.2.1, in
# datashare-app/src/main/java/org/icij/datashare/web/IndexResource.java and
# datashare-app/src/main/java/org/icij/datashare/utils/IndexAccessVerifier.java:
#
#   1. IndexResource is @Prefix("/api/index") — the same prefix as the two endpoints
#      below that this server legitimately calls. It also registers, on that prefix:
#        PUT     /api/index/:index                                (createIndex)
#        POST    /api/index/:index/_close                         (takes the corpus offline)
#        POST    /api/index/:index/_open
#        PUT     /api/index/_snapshot/:repository
#        DELETE  /api/index/_snapshot/:repository
#        PUT     /api/index/_snapshot/:repository/:snapshot       (createSnapshot)
#        DELETE  /api/index/_snapshot/:repository/:snapshot
#        POST    /api/index/_snapshot/:repository/:snapshot/_restore
#        HEAD    /api/index/search/:path:
#        OPTIONS /api/index/:index  and  OPTIONS /api/index/search/:path:
#      Every one of those except createIndex and the HEAD/OPTIONS pair is guarded only by
#      `modeVerifier.checkAllowedMode(Mode.LOCAL, Mode.EMBEDDED)` — which admits the
#      LOCAL mode this server is deployed against. createIndex is the one route in the
#      class with no mode check at all. Nothing else protects them. The path pin below is
#      the whole of the defence.
#
#   2. @Post("/search/:path:") is a greedy raw Elasticsearch proxy; its own javadoc says
#      "everything sent is forwarded to Elasticsearch". IndexAccessVerifier.checkPath
#      narrows POST via `areAllIndexesGranted && (isMethodGet || isSearchPath ||
#      isCountPath)`, so _delete_by_query is refused server-side *today* — but that is a
#      property of this Datashare version and mode, not a promise to this client. Pinning
#      POST to the literal _search suffix makes the guarantee ours.
#
#   3. In that same expression `isMethodGet` is an unconditional OR branch: once the
#      index-name and grant checks pass, a GET is forwarded to *any* Elasticsearch path
#      under them (the upstream comment reads "As it is a GET method, all paths are
#      accepted"). Pinning GET to the literal _mapping suffix is therefore load-bearing,
#      not decorative.
#
#   4. checkPath short-circuits on isSearchScrollPath: a path beginning `_search/scroll`
#      returns before any grant check at all. The allowlist refuses that shape anyway,
#      because no pair matches it.
#
# LOCAL mode does not enforce API-key authentication either (LocalMode binds only
# CsrfFilter and LocalUserFilter; ServerMode is the one that adds ApiKeyFilter), so "the
# key is read-only" is not an available defence and this allowlist is the only one.
#
# client.py builds its input validator `_SAFE_PATH_SEGMENT` from _SEGMENT, so the
# validator and this guard cannot disagree by construction. Excluding `/` is what keeps
# /api/{project}/documents/{doc_id} from also swallowing
# /api/{project}/documents/content/{doc_id}.
#
# A segment must carry at least one non-dot character, which is what excludes the literal
# `.` and `..`. Without that, two pairs below fullmatch `/api/../documents/d1` and the
# guard would *authorise* a path escaping the /api namespace. Today httpx happens to
# collapse dot segments inside build_request, before the event hook runs — but that is an
# undocumented ordering property of a dependency pinned only as `httpx>=0.27,<1`, so it is
# not something this guard may rely on. It also removes an observable confusion that does
# not need a future httpx to bite: get_document_metadata(project="p", doc_id="..") built
# /api/p/documents/.., which normalises to /api/p — a tool documented to return one
# document's metadata returning something else entirely.
#
# Written as "any run containing one non-dot" rather than a `(?!\.+$)` lookahead because
# _SEGMENT is interpolated into longer patterns, where `$` would anchor at the end of the
# whole path instead of the end of the segment.
_SEGMENT = r"[A-Za-z0-9._-]*[A-Za-z0-9_-][A-Za-z0-9._-]*"

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

    `path` is the wire path relative to the Datashare root, i.e. with any base-URL prefix
    already removed and *not* percent-decoded — see `read_only_hook`.

    Never drop the method from a pair. `/api/{project}/documents/{doc_id}` sits in a
    namespace that carries mutating routes upstream — `PUT /:project/documents/tags/:docId`,
    `PUT /:project/documents/untag/:docId` and `POST /:project/documents/batchUpdate/*` in
    DocumentResource.java at 21.2.1. None is reachable through this pair's single-segment
    shape today; the method pin is what keeps that true if upstream ever registers a
    mutating handler on the identical path.
    """
    return any(m == method and p.fullmatch(path) for m, p in _ALLOWED)


def _safe_target(request: httpx.Request) -> str:
    """`METHOD scheme://host[:port]/path`, with any userinfo and query dropped.

    `str(httpx.URL)` renders userinfo verbatim — only `__repr__` substitutes `[secure]` —
    and httpx derives BasicAuth from URL userinfo, so a DATASHARE_URL of
    `https://user:pass@host` is a working configuration whose password would otherwise be
    interpolated into a refusal message and handed to the model. The query string is
    dropped for the same reason: it is caller-influenced and adds nothing to a refusal.
    """
    url = request.url
    port = "" if url.port is None else f":{url.port}"
    path = url.raw_path.partition(b"?")[0].decode("ascii", "replace")
    return f"{request.method} {url.scheme}://{url.host}{port}{path}"


def read_only_hook(url: str) -> Callable[[httpx.Request], Awaitable[None]]:
    """Build the httpx request hook that pins every request to `url`'s origin and the allowlist.

    The returned hook runs for every request the client sends, redirect hops included, so
    the guarantee holds regardless of what the API key or the instance's mode permits
    server-side. It refuses a request that leaves the configured origin — a 307 preserves
    method and body, and this client attaches the bearer key to every request, so a
    redirect would otherwise replay both somewhere the operator never configured — and it
    strips the configured base path before matching, so a Datashare mounted under
    https://example.org/datashare is checked on /api/... like any other.

    The origin comparison is (scheme, host, port), not host alone. Host alone would admit
    a hop to a different service on the same machine — in the documented local topology,
    port 9201 is the Elasticsearch container — and would admit an https→http downgrade
    that puts the bearer key on the wire in cleartext.

    Matching uses `raw_path`, the bytes httpx actually sends, NOT the percent-decoded
    `request.url.path`. The two diverge: httpx's `quote()` preserves pre-existing `%xx`
    escapes on the wire while `URL.path` decodes them, so matching the decoded form would
    authorise one string and send another — `/api/%2e%2e/documents/d1` decodes to
    `/api/../documents/d1`, which fullmatches, while the wire carries the encoded form.
    Decoding cannot be assumed to only add separators: `%2e` and `%72` decode to `.` and
    `r`, both inside `_SEGMENT`. Matching the raw form is strictly tighter, and costs
    nothing legitimate because `client._validate_path_segment` already rejects `%` in
    every caller-supplied segment.
    """
    base = httpx.URL(url)
    expected_origin = (base.scheme, base.host, base.port)
    prefix = base.path.rstrip("/")

    async def enforce_read_only(request: httpx.Request) -> None:
        origin = (request.url.scheme, request.url.host, request.url.port)
        if origin != expected_origin:
            raise ReadOnlyViolation(
                f"blocked {_safe_target(request)}: this request leaves the "
                f"configured Datashare host {base.scheme}://{base.host}"
                f"{'' if base.port is None else f':{base.port}'}"
            )
        path = request.url.raw_path.partition(b"?")[0].decode("ascii", "replace")
        if prefix:
            if path == prefix:
                path = "/"
            elif path.startswith(f"{prefix}/"):
                path = path[len(prefix) :]
            else:
                raise ReadOnlyViolation(
                    f"blocked {_safe_target(request)}: this request leaves the "
                    f"configured Datashare base path {prefix}"
                )
        if not is_read_only(request.method, path):
            raise ReadOnlyViolation(
                f"blocked {_safe_target(request)}: datashare-mcp is read-only and "
                "only calls a fixed allowlist of Datashare read endpoints"
            )

    return enforce_read_only
