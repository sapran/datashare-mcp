from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import Any, cast

import httpx

from .config import Settings
from .errors import raise_for_status
from .readonly import _SEGMENT, read_only_hook

# Built from the guard's own charset, not a second copy of it: the input validator and
# readonly.py cannot drift apart. `fullmatch`, not `match` with `^...$` — Python's `$`
# also matches immediately before a trailing newline, which would admit "tenderchad\n".
_SAFE_PATH_SEGMENT = re.compile(_SEGMENT)


def _validate_path_segment(value: str, *, field: str) -> str:
    if not isinstance(value, str) or not _SAFE_PATH_SEGMENT.fullmatch(value):
        raise ValueError(f"invalid {field}: must match {_SEGMENT} (got {value!r})")
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
            event_hooks={"request": [read_only_hook(settings.url)]},
            timeout=settings.timeout_secs,
            verify=settings.verify_tls,
        )

    async def aclose(self) -> None:
        await self._http.aclose()

    async def list_projects(self) -> list[dict[str, Any]]:
        resp = await self._http.get("/api/project/")
        raise_for_status(resp, context="list_projects")
        return cast(list[dict[str, Any]], resp.json())

    async def search(self, *, project: str, query: dict[str, Any]) -> dict[str, Any]:
        _validate_path_segment(project, field="project")
        resp = await self._http.post(
            f"/api/index/search/{project}/_search",
            json=query,
        )
        raise_for_status(resp, context="search_documents")
        return cast(dict[str, Any], resp.json())

    async def get_document_metadata(
        self, *, project: str, doc_id: str, routing: str | None = None
    ) -> dict[str, Any]:
        _validate_path_segment(project, field="project")
        _validate_path_segment(doc_id, field="doc_id")
        params = {"routing": routing} if routing else None
        resp = await self._http.get(f"/api/{project}/documents/{doc_id}", params=params)
        raise_for_status(resp, context="get_document_metadata")
        return cast(dict[str, Any], resp.json())

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
        return cast(dict[str, Any], resp.json())

    async def get_mapping(self, *, project: str) -> dict[str, Any]:
        _validate_path_segment(project, field="project")
        resp = await self._http.get(f"/api/index/search/{project}/_mapping")
        raise_for_status(resp, context=f"datashare://index/{project}/mapping", resource=True)
        return cast(dict[str, Any], resp.json())

    async def get_project_summary(self, *, project: str, format: str = "json") -> dict[str, Any]:
        """Generate comprehensive project summary combining all analyses."""
        _validate_path_segment(project, field="project")

        overview = await self.get_project_overview(project=project)
        type_dist = await self.get_document_type_distribution(project=project)
        temporal = await self.get_temporal_distribution(project=project)

        path_analysis = await self._analyze_document_structure(project)
        quality_assessment = await self._assess_data_quality(project, type_dist)
        insights = self._generate_insights(temporal, quality_assessment, type_dist)

        summary = {
            "metadata": {
                "generatedAt": datetime.now(UTC).isoformat(),
                "project": project,
                "version": "1.0",
            },
            "overview": {
                "totalDocuments": overview["totalDocuments"],
                "languageDistribution": overview["languageDistribution"],
                "dateRange": overview["dateRange"],
            },
            "documentTypes": type_dist["typeDistribution"],
            "temporalDistribution": temporal["yearlyDistribution"],
            "structure": path_analysis,
            "quality": quality_assessment,
            "insights": insights,
        }

        if format == "markdown":
            return {"markdown": self._render_markdown(summary)}

        return summary

    async def _analyze_document_structure(self, project: str) -> dict[str, Any]:
        """Analyze document path patterns."""
        query = {"size": 50, "query": {"match_all": {}}, "_source": ["path", "dirname"]}

        resp = await self._http.post(
            f"/api/index/search/{project}/_search",
            json=query,
        )
        raise_for_status(resp, context="_analyze_document_structure")
        result = resp.json()

        paths = [hit["_source"].get("path", "") for hit in result.get("hits", {}).get("hits", [])]

        tender_pattern = "/media/docs/"
        tender_ids = set()
        for path in paths:
            if tender_pattern in path:
                idx = path.find(tender_pattern) + len(tender_pattern)
                tender_id = path[idx:].split("/")[0]
                if tender_id:
                    tender_ids.add(tender_id)

        return {
            "detectedPattern": f"<tender_id> under {tender_pattern}",
            "sampleTenderIds": sorted(list(tender_ids))[:10],
            "totalUniqueTenders": len(tender_ids),
        }

    async def _assess_data_quality(self, project: str, type_dist: dict[str, Any]) -> dict[str, Any]:
        """Assess data quality based on document types."""
        total = type_dist.get("totalDocuments", 0)
        type_dist_list = type_dist.get("typeDistribution", [])

        image_count = sum(t["count"] for t in type_dist_list if t["type"] == "Images")
        image_pct = round(image_count / total * 100, 1) if total > 0 else 0

        issues = []
        if image_pct > 10:
            issues.append(
                {
                    "type": "embedded_images",
                    "severity": "medium",
                    "description": f"{image_count} image files ({image_pct}% of corpus) may need separate handling",
                    "recommendation": "Consider OCR for image-heavy documents",
                }
            )

        return {
            "totalDocuments": total,
            "imageCount": image_count,
            "imagePercentage": image_pct,
            "issues": issues,
            "extractionSuccessRate": "unknown",
        }

    def _generate_insights(
        self,
        temporal: dict[str, Any],
        quality: dict[str, Any],
        type_dist: dict[str, Any],
    ) -> list[dict[str, Any]]:
        """Generate automatic insights from analysis."""
        insights = []

        peaks = temporal.get("peaks", [])
        if peaks:
            latest_peak = max(peaks, key=lambda x: x["year"])
            insights.append(
                {
                    "type": "temporal_spike",
                    "title": f"Major activity spike in {latest_peak['year']}",
                    "description": latest_peak["note"],
                    "severity": "info",
                }
            )

        if quality.get("imagePercentage", 0) > 15:
            insights.append(
                {
                    "type": "data_quality",
                    "title": "High image content ratio",
                    "description": f"{quality['imagePercentage']}% of documents are images",
                    "severity": "warning",
                }
            )

        type_list = type_dist.get("typeDistribution", [])
        word_docs = next((t for t in type_list if t["type"] == "Word Documents"), None)
        if word_docs and word_docs["percentage"] > 50:
            insights.append(
                {
                    "type": "content_pattern",
                    "title": "Word documents dominate",
                    "description": f"{word_docs['percentage']}% are Word documents",
                    "severity": "info",
                }
            )

        suggested_use_cases = []
        if word_docs and word_docs["percentage"] > 30:
            suggested_use_cases.append("Contract analysis and extraction")
        if quality.get("imageCount", 0) > 100:
            suggested_use_cases.append("Document digitization/OCR pipeline")
        if len(peaks) > 2:
            suggested_use_cases.append("Temporal trend analysis")

        if suggested_use_cases:
            insights.append(
                {
                    "type": "suggestions",
                    "title": "Suggested use cases",
                    "description": suggested_use_cases,
                    "severity": "info",
                }
            )

        return insights

    def _render_markdown(self, summary: dict[str, Any]) -> str:
        """Render summary as markdown with ASCII visualizations."""
        lines = []

        overview = summary["overview"]
        lines.append(f"# Project Summary: {summary['metadata']['project']}")
        lines.append("")
        lines.append(f"**Generated:** {summary['metadata']['generatedAt']}")
        lines.append("")

        lines.append("## Overview")
        lines.append("")
        lines.append(f"- **Total Documents:** {overview['totalDocuments']:,}")

        lang_dist = overview["languageDistribution"][:5]
        langs = ", ".join(f"{d['language']}: {d['count']:,}" for d in lang_dist)
        lines.append(f"- **Languages:** {langs}")

        date_range = overview["dateRange"]
        if date_range["min"] and date_range["max"]:
            min_year = datetime.fromtimestamp(date_range["min"] / 1000, tz=UTC).year
            max_year = datetime.fromtimestamp(date_range["max"] / 1000, tz=UTC).year
            if min_year > 1900:
                lines.append(f"- **Date Range:** {min_year} - {max_year}")
            else:
                lines.append(f"- **Date Range:** unknown - {max_year}")
        lines.append("")

        lines.append("## Document Types")
        lines.append("")
        lines.append("| Type | Count | Percentage |")
        lines.append("|------|-------|------------|")
        for t in summary["documentTypes"][:10]:
            lines.append(f"| {t['type']} | {t['count']:,} | {t['percentage']}% |")
        lines.append("")

        lines.append("## Temporal Distribution")
        lines.append("")
        yearly = summary["temporalDistribution"][-10:]
        max_count = max((y["count"] for y in yearly), default=1)

        for entry in yearly:
            bar_len = int((entry["count"] / max_count) * 40) if max_count > 0 else 0
            bar = "█" * bar_len
            lines.append(f"{entry['year']}: {bar} {entry['count']:,}")
        lines.append("")

        if summary["insights"]:
            lines.append("## Insights")
            lines.append("")
            for insight in summary["insights"]:
                lines.append(f"### {insight.get('title', insight['type'])}")
                if isinstance(insight.get("description"), list):
                    for item in insight["description"]:
                        lines.append(f"- {item}")
                else:
                    lines.append(f"{insight.get('description', '')}")
                lines.append("")

        return "\n".join(lines)

    async def get_project_overview(self, *, project: str) -> dict[str, Any]:
        """Get high-level project statistics: total count, languages, date range."""
        _validate_path_segment(project, field="project")

        query = {
            "size": 0,
            "aggs": {
                "by_language": {"terms": {"field": "language", "size": 100}},
                "min_date": {"min": {"field": "creationDate"}},
                "max_date": {"max": {"field": "creationDate"}},
            },
        }

        resp = await self._http.post(
            f"/api/index/search/{project}/_search",
            json=query,
        )
        raise_for_status(resp, context="get_project_overview")
        result = resp.json()

        aggs = result.get("aggregations", {})
        hits = result.get("hits", {})

        return {
            "totalDocuments": hits.get("total", {}).get("value", 0),
            "languageDistribution": [
                {"language": b["key"], "count": b["doc_count"]}
                for b in aggs.get("by_language", {}).get("buckets", [])
            ],
            "dateRange": {
                "min": aggs.get("min_date", {}).get("value"),
                "max": aggs.get("max_date", {}).get("value"),
            },
        }

    async def get_document_type_distribution(self, *, project: str) -> dict[str, Any]:
        """Analyze document content types with counts and percentages."""
        _validate_path_segment(project, field="project")

        total_docs = await self._get_total_count(project)

        query = {"size": 0, "aggs": {"by_type": {"terms": {"field": "contentType", "size": 100}}}}

        resp = await self._http.post(
            f"/api/index/search/{project}/_search",
            json=query,
        )
        raise_for_status(resp, context="get_document_type_distribution")
        result = resp.json()

        aggs = result.get("aggregations", {})
        buckets = aggs.get("by_type", {}).get("buckets", [])

        type_groups = {
            "Word Documents": [
                "application/msword",
                "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            ],
            "Excel Spreadsheets": [
                "application/vnd.ms-excel",
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                "application/vnd.ms-excel.sheet.macroenabled.12",
            ],
            "PDF Documents": ["application/pdf"],
            "Images": [
                "image/wmf",
                "image/png",
                "image/emf",
                "image/jpeg",
                "image/unknown",
                "image/svg+xml",
                "image/bmp",
                "image/tiff",
                "image/vnd.microsoft.icon",
            ],
            "Web Files": ["text/html", "text/css", "text/javascript"],
            "Archive Files": [
                "application/zip",
                "application/x-rar-compressed",
                "application/gzip",
            ],
            "OpenDocument": [
                "application/vnd.oasis.opendocument.text",
                "application/vnd.oasis.opendocument.spreadsheet",
            ],
            "Other": [],
        }

        grouped: dict[str, int] = {}
        for bucket in buckets:
            content_type = bucket["key"]
            count = bucket["doc_count"]
            assigned = False

            for group_name, type_list in type_groups.items():
                if content_type in type_list:
                    grouped[group_name] = grouped.get(group_name, 0) + count
                    assigned = True
                    break

            if not assigned:
                grouped["Other"] = grouped.get("Other", 0) + count

        result_list = []
        for group_name, count in sorted(grouped.items(), key=lambda x: -x[1]):
            result_list.append(
                {
                    "type": group_name,
                    "count": count,
                    "percentage": round(count / total_docs * 100, 1) if total_docs > 0 else 0,
                }
            )

        return {"totalDocuments": total_docs, "typeDistribution": result_list}

    async def _get_total_count(self, project: str) -> int:
        """Get total document count for a project."""
        query = {"size": 0}
        resp = await self._http.post(
            f"/api/index/search/{project}/_search",
            json=query,
        )
        raise_for_status(resp, context="_get_total_count")
        result = cast(dict[str, Any], resp.json())
        return cast(int, result.get("hits", {}).get("total", {}).get("value", 0))

    async def get_temporal_distribution(self, *, project: str) -> dict[str, Any]:
        """Get document counts grouped by year with peak detection."""
        _validate_path_segment(project, field="project")

        total_docs = await self._get_total_count(project)

        query = {
            "size": 0,
            "aggs": {
                "by_year": {
                    "date_histogram": {
                        "field": "creationDate",
                        "calendar_interval": "year",
                        "format": "yyyy",
                        "min_doc_count": 1,
                    }
                }
            },
        }

        resp = await self._http.post(
            f"/api/index/search/{project}/_search",
            json=query,
        )
        raise_for_status(resp, context="get_temporal_distribution")
        result = resp.json()

        aggs = result.get("aggregations", {})
        buckets = aggs.get("by_year", {}).get("buckets", [])

        yearly_data = []
        for bucket in buckets:
            epoch_ms = bucket.get("key", 0)
            if epoch_ms and epoch_ms > 0:
                try:
                    year = datetime.fromtimestamp(epoch_ms / 1000, tz=UTC).year
                    yearly_data.append({"year": year, "count": bucket["doc_count"]})
                except (ValueError, OSError):
                    pass

        yearly_data.sort(key=lambda x: x["year"])

        counts = [d["count"] for d in yearly_data if d["year"] > 0]
        median_count = sorted(counts)[len(counts) // 2] if counts else 0

        peaks = []
        for entry in yearly_data:
            if entry["year"] > 0 and entry["count"] > median_count * 2:
                peaks.append(
                    {
                        "year": entry["year"],
                        "count": entry["count"],
                        "note": f"Significant spike: {entry['count']} docs ({round(entry['count'] / total_docs * 100, 1)}% of corpus)",
                    }
                )

        return {
            "totalDocuments": total_docs,
            "yearlyDistribution": yearly_data,
            "peaks": peaks,
            "medianCount": median_count,
        }
