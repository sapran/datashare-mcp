from __future__ import annotations

import re
from typing import Any

import httpx

from .config import Settings
from .errors import raise_for_status

_SAFE_PATH_SEGMENT = re.compile(r"^[A-Za-z0-9._-]+$")


def _validate_path_segment(value: str, *, field: str) -> str:
    if not isinstance(value, str) or not _SAFE_PATH_SEGMENT.match(value):
        raise ValueError(f"invalid {field}: must match [A-Za-z0-9._-]+ (got {value!r})")
    return value


class DatashareClient:
    """Async wrapper around the datashare REST API.

    Owns one httpx.AsyncClient. Caller is responsible for closing it via aclose().
    All non-2xx responses are converted to ToolError/ResourceError via raise_for_status.
    """

    def __init__(self, settings: Settings):
        self._settings = settings
        # Datashare's CsrfFilter requires non-GET /api/* requests to carry a matching
        # cookie + X-DS-CSRF-TOKEN header, even when authenticated by bearer API key.
        # The filter only checks equality, so a fixed value is sufficient.
        csrf_token = "datashare-mcp"
        self._http = httpx.AsyncClient(
            base_url=settings.url,
            headers={
                "Authorization": f"Bearer {settings.api_key}",
                "X-DS-CSRF-TOKEN": csrf_token,
            },
            cookies={"_ds_csrf_token": csrf_token},
            timeout=settings.timeout_secs,
            verify=settings.verify_tls,
        )

    async def aclose(self) -> None:
        await self._http.aclose()

    async def list_projects(self) -> list[dict[str, Any]]:
        resp = await self._http.get("/api/project/")
        raise_for_status(resp, context="list_projects")
        return resp.json()

    async def search(self, *, project: str, query: dict[str, Any]) -> dict[str, Any]:
        _validate_path_segment(project, field="project")
        resp = await self._http.post(
            f"/api/index/search/{project}/_search",
            json=query,
        )
        raise_for_status(resp, context="search_documents")
        return resp.json()

    async def get_document_metadata(
        self, *, project: str, doc_id: str, routing: str | None = None
    ) -> dict[str, Any]:
        _validate_path_segment(project, field="project")
        _validate_path_segment(doc_id, field="doc_id")
        params = {"routing": routing} if routing else None
        resp = await self._http.get(f"/api/{project}/documents/{doc_id}", params=params)
        raise_for_status(resp, context="get_document_metadata")
        return resp.json()

    async def get_document_content(
        self,
        *,
        project: str,
        doc_id: str,
        routing: str | None = None,
        offset: int | None = None,
        limit: int | None = None,
        target_language: str | None = None,
        resource: bool = False,
    ) -> dict[str, Any]:
        _validate_path_segment(project, field="project")
        _validate_path_segment(doc_id, field="doc_id")
        if (offset is None) != (limit is None):
            raise ValueError("offset and limit must be supplied together (or both omitted)")
        if offset is None and limit is None:
            # Full content. Datashare's no-range endpoint reads the whole text from
            # the relational DB, which is empty for index-only documents (CLI SCAN/INDEX
            # populates Elasticsearch, not the DB) and returns HTTP 500. Use the
            # Elasticsearch-backed ranged path instead: probe with limit=0 (a no-op
            # substring(0,0) that still reports `maxOffset`) to learn the length, then
            # fetch the whole range in one call.
            probe = await self._get_content_range(
                project, doc_id, routing, 0, 0, target_language, resource
            )
            max_offset = probe.get("maxOffset", 0) or 0
            if max_offset <= 0:
                return probe
            return await self._get_content_range(
                project, doc_id, routing, 0, max_offset, target_language, resource
            )
        # Both supplied (the half-supplied and both-omitted cases returned above).
        assert offset is not None and limit is not None
        return await self._get_content_range(
            project, doc_id, routing, offset, limit, target_language, resource
        )

    async def _get_content_range(
        self,
        project: str,
        doc_id: str,
        routing: str | None,
        offset: int,
        limit: int,
        target_language: str | None,
        resource: bool,
    ) -> dict[str, Any]:
        params: dict[str, Any] = {"offset": offset, "limit": limit}
        if routing is not None:
            params["routing"] = routing
        if target_language is not None:
            params["targetLanguage"] = target_language
        resp = await self._http.get(
            f"/api/{project}/documents/content/{doc_id}",
            params=params,
        )
        raise_for_status(resp, context="get_document_content", resource=resource)
        return resp.json()

    async def get_mapping(self, *, project: str) -> dict[str, Any]:
        _validate_path_segment(project, field="project")
        resp = await self._http.get(f"/api/index/search/{project}/_mapping")
        raise_for_status(resp, context=f"datashare://index/{project}/mapping", resource=True)
        return resp.json()
