from __future__ import annotations

from typing import Any

import httpx

from .config import Settings
from .errors import raise_for_status


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
        resp = await self._http.post(
            f"/api/index/search/{project}/_search",
            json=query,
        )
        raise_for_status(resp, context="search_documents")
        return resp.json()

    async def get_document_metadata(
        self, *, project: str, doc_id: str, routing: str | None = None
    ) -> dict[str, Any]:
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
        if (offset is None) != (limit is None):
            raise ValueError("offset and limit must be supplied together (or both omitted)")
        params: dict[str, Any] = {}
        if routing is not None:
            params["routing"] = routing
        if offset is not None:
            params["offset"] = offset
            params["limit"] = limit
        if target_language is not None:
            params["targetLanguage"] = target_language
        resp = await self._http.get(
            f"/api/{project}/documents/content/{doc_id}",
            params=params or None,
        )
        raise_for_status(resp, context="get_document_content", resource=resource)
        return resp.json()

    async def get_mapping(self, *, project: str) -> dict[str, Any]:
        resp = await self._http.get(f"/api/index/search/{project}/_mapping")
        raise_for_status(resp, context=f"datashare://index/{project}/mapping", resource=True)
        return resp.json()
