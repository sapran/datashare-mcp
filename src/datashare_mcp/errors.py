from __future__ import annotations

import json
import logging
import re

import httpx
from fastmcp.exceptions import ResourceError, ToolError

from .readonly import ReadOnlyViolation

# Everything a client call can raise that is expected rather than a bug. Tools caught
# only ValueError, so a guard refusal (a RuntimeError) and every httpx transport error
# escaped to FastMCP, which does not mask exception text by default.
CLIENT_FAILURES = (ValueError, ReadOnlyViolation, httpx.HTTPError)

logger = logging.getLogger("datashare_mcp")

# An Elasticsearch error type is a snake_case token. Anything else is not one, and is
# therefore not shown.
_ERROR_TYPE = re.compile(r"[a-z_]{1,64}")


def failure_message(e: Exception, *, context: str) -> str:
    """Render an expected client failure for the model, without leaking internals.

    ValueError is this server's own argument validation, so its text is ours to show.
    The other two are not: a guard refusal names the internal request target and a
    transport error names the resolved host, connection and TLS details, none of which
    the caller supplied or can act on.
    """
    if isinstance(e, ValueError):
        return str(e)
    if isinstance(e, ReadOnlyViolation):
        return (
            f"{context}: refused by the read-only guard. datashare-mcp calls a fixed "
            "allowlist of Datashare read endpoints and nothing else."
        )
    return f"{context}: could not reach the configured Datashare instance."


def raise_for_status(
    resp: httpx.Response, *, context: str, body: str, resource: bool = False
) -> None:
    """Convert non-2xx HTTP responses to MCP errors. No-op for 2xx.

    `context` is a short label (tool name or resource URI) included in the message
    so the model can correlate the failure with the call site.

    The upstream body never reaches the model. Datashare 5xx bodies carry Java stack
    traces with internal class and file paths, and an Elasticsearch 4xx `reason` names
    the index, field and analyzer chain — and `search_documents` lets the caller choose
    the body that provokes it. The body goes to the debug log, where the operator can
    read it; the model gets the status, a fixed hint, and at most a whitelisted
    `error.type` token.
    """
    if resp.is_success:
        return

    err_cls = ResourceError if resource else ToolError
    # Required, not defaulted to resp.text: every response this server produces is drained
    # via `aiter_bytes()`, and `resp.text` on such a response raises ResponseNotRead. A
    # fallback would invite the next call site to omit it and fail on the error path.
    text = body
    # Bounded: the log is a debugging aid, not a place to spool an unbounded upstream
    # body into the operator's terminal or log file.
    logger.debug("%s: HTTP %s from Datashare: %.4096s", context, resp.status_code, text)

    if resp.status_code == 401:
        raise err_cls(
            f"{context}: API key invalid or expired (401). Verify DATASHARE_API_KEY is current."
        )
    if resp.status_code == 403:
        raise err_cls(
            f"{context}: API key valid but lacks access (403). "
            "Check that the user has been granted the requested project in datashare."
        )
    if resp.status_code == 404:
        raise err_cls(
            f"{context}: not found (404). If the project name is wrong, call list_projects."
        )
    if resp.status_code == 400:
        kind = _error_type(text)
        raise err_cls(
            f"{context}: bad request (400){'' if kind is None else f' [{kind}]'}. "
            "Datashare rejected the request; check the query against the project mapping "
            "at datashare://index/{project}/mapping."
        )
    raise err_cls(
        f"{context}: the Datashare instance returned HTTP {resp.status_code}. "
        "Its response was written to the server's debug log."
    )


def _error_type(text: str) -> str | None:
    """The Elasticsearch `error.type` token, if the body carries one in the expected shape.

    Only `type` — never `reason`, which is free text naming index, field and analyzer.
    The charset check is what makes this a whitelist rather than a narrower echo: a
    token like `parsing_exception` passes, anything else is dropped entirely.
    """
    try:
        payload = json.loads(text)
    except ValueError:
        return None
    if not isinstance(payload, dict):
        return None
    error = payload.get("error")
    if not isinstance(error, dict):
        return None
    kind = error.get("type")
    if isinstance(kind, str) and _ERROR_TYPE.fullmatch(kind):
        return kind
    return None
