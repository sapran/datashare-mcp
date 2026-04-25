from __future__ import annotations

import httpx
from fastmcp.exceptions import ResourceError, ToolError


def raise_for_status(resp: httpx.Response, *, context: str, resource: bool = False) -> None:
    """Convert non-2xx HTTP responses to MCP errors. No-op for 2xx.

    `context` is a short label (tool name or resource URI) included in the message
    so the model can correlate the failure with the call site.
    """
    if resp.is_success:
        return

    err_cls = ResourceError if resource else ToolError
    body = _truncate(resp.text, 500)

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
        raise err_cls(f"{context}: bad request (400). {body}")
    raise err_cls(f"{context}: unexpected HTTP {resp.status_code}. {body}")


def _truncate(s: str, n: int) -> str:
    if len(s) <= n:
        return s
    return s[:n] + "…"
