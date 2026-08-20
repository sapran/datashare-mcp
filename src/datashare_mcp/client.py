from __future__ import annotations

import asyncio
import json
import re
import ssl
from datetime import UTC, datetime
from typing import Any, cast

import httpx

from .config import Settings
from .errors import raise_for_status
from .readonly import _SEGMENT, read_only_hook

# Built from the guard's own charset, not a second copy of it: the input validator and
# readonly.py cannot drift apart. `fullmatch`, not `match` with `^...$` — Python's `$`
# also matches immediately before a trailing newline, which would admit "demo\n".
_SAFE_PATH_SEGMENT = re.compile(_SEGMENT)


# Elasticsearch reads these as "every index", so they would address the whole cluster
# through a path shaped like one project. No Datashare project is named this way; what
# stops them today is Datashare's own grant check, which readonly.py argues at length is
# not a defence this client may rely on.
_RESERVED_INDEX_NAMES = frozenset({"_all", "_any", "_none"})


def _validate_path_segment(value: str, *, field: str) -> str:
    if not isinstance(value, str) or not _SAFE_PATH_SEGMENT.fullmatch(value):
        raise ValueError(f"invalid {field}: must match {_SEGMENT} (got {value!r})")
    if field == "project" and value.lower() in _RESERVED_INDEX_NAMES:
        raise ValueError(
            f"invalid project: {value!r} is an Elasticsearch alias for every index, "
            "not a project; call list_projects"
        )
    return value


# The path allowlist in readonly.py pins POST to the literal `_search` suffix, but it
# never looks at the body, and Datashare's @Post("/search/:path:") javadoc says
# "everything sent is forwarded to Elasticsearch". These are the body-side features that
# escape the guarantee the path pin makes:
#
#   * Painless execution on the Elasticsearch node. Read-only in intent, arbitrary
#     computation in fact.
#   * a clause carrying a document *reference* — {"index": "other-project", "id": "1"} —
#     which reads a document out of an index this request's path never named, so
#     Datashare's path-based grant check does not cover it.
#
# Scripting is matched by RULE, not by an enumerated list. An earlier version listed four
# spellings — script, script_fields, script_score, runtime_mappings — and a
# `scripted_metric` aggregation walked straight through it, because its Painless lives
# under init_script / map_script / combine_script / reduce_script.
#
# Matched at a TOKEN BOUNDARY, not as a bare substring. Every script-bearing DSL key is
# either `script...` or `..._script`; a bare `"script" in key` also rejects `description`
# — and therefore `{"match": {"description": ...}}`, plus every Tika-derived
# `..._dc_description` field — with a message about server-side scripting that gives the
# caller nothing to act on. The refusal must land on the construct, not on the letters.
#
# `runtime_mappings` is the one script-bearing construct whose name contains no `script`
# token, and `more_like_this` fetches documents by reference, so both stay named.
_SCRIPT_KEY = re.compile(r"(?:^|_)script")
_FORBIDDEN_BODY_KEYS = frozenset({"runtime_mappings", "more_like_this"})
# A cross-index document reference, matched by SHAPE rather than by the name of the clause
# holding it. An earlier version keyed this on the parent being literally `terms`, which
# caught the terms lookup and missed every other spelling of the same primitive:
# `geo_shape.indexed_shape` uses the identical {index, id, path, routing} keys under a
# different parent, `percolate` uses {index, id}, and `pinned.docs` uses {_index, _id}.
# All of them make Elasticsearch read a document out of an index the request path never
# named, which is the property this rule exists to deny — so the rule is the shape.
_DOC_REFERENCE_KEYS: tuple[frozenset[str], ...] = (
    frozenset({"index", "id"}),
    frozenset({"_index", "_id"}),
)
# Bounds the recursive scan. Deeper than this is not a query anyone writes by hand, and
# an unbounded walk on caller-supplied JSON is its own denial of service.
_MAX_BODY_DEPTH = 32


def _scan_body(node: Any, *, depth: int = 0) -> None:
    """Reject forbidden constructs anywhere in an Elasticsearch request body."""
    if depth > _MAX_BODY_DEPTH:
        raise ValueError(f"invalid query: nested deeper than {_MAX_BODY_DEPTH} levels")
    if isinstance(node, dict):
        if any(required <= node.keys() for required in _DOC_REFERENCE_KEYS):
            raise ValueError(
                "invalid query: a document lookup reads from an index outside the "
                "project in the request path; pass literal values instead"
            )
        for key, value in node.items():
            if isinstance(key, str) and (
                _SCRIPT_KEY.search(key.lower()) or key in _FORBIDDEN_BODY_KEYS
            ):
                raise ValueError(
                    f"invalid query: {key!r} is not allowed — this server forwards only "
                    "declarative read queries, not server-side scripting"
                )
            _scan_body(value, depth=depth + 1)
    elif isinstance(node, list):
        for item in node:
            _scan_body(item, depth=depth + 1)


def _bounded_int(value: Any, *, key: str, max_size: int, clamp: bool) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"invalid query: {key!r} must be an integer")
    if value < 0:
        raise ValueError(f"invalid query: {key!r} must not be negative")
    if not clamp and value > max_size:
        raise ValueError(
            f"invalid query: {key!r} of {value} exceeds the configured maximum "
            f"{max_size}; narrow the query, or sort and use search_after"
        )
    return min(value, max_size)


def _clamp_agg_sizes(node: Any, *, max_size: int) -> Any:
    """Copy an `aggs` subtree, clamping every `size` it contains.

    Inside an aggregation, `size` is always a cardinality — on `terms`, on `top_hits`, on
    a nested `aggs` — so bounding it here is safe and is what stops
    `{"terms": {"field": "_id", "size": 100000}}` loading every document id into fielddata
    on a 1 GB heap.
    """
    if isinstance(node, dict):
        return {
            key: (
                _bounded_int(value, key=key, max_size=max_size, clamp=True)
                if key == "size"
                else _clamp_agg_sizes(value, max_size=max_size)
            )
            for key, value in node.items()
        }
    if isinstance(node, list):
        return [_clamp_agg_sizes(item, max_size=max_size) for item in node]
    return node


def _validate_search_body(query: Any, *, max_size: int) -> dict[str, Any]:
    """Return a safe copy of an Elasticsearch request body, or raise ValueError.

    `size` and `from` are pagination ONLY at the top level of the body, and `size` again
    inside `aggs`. Everywhere else they are field names or range bounds a corpus query
    legitimately uses — `{"term": {"from": "alice@example.com"}}` on an email corpus,
    `{"range": {"creationDate": {"from": "2020-01-01"}}}` — so the walk must not treat
    them as pagination. Descending blindly turned both of those into hard errors.

    Top-level `size` is clamped; top-level `from` is refused. A clamped page size returns
    fewer hits and `hits.total` still reports the truth. A clamped `from` is a cursor
    silently moved: the caller asked for hits 1000-1020 and would receive 200-220, with
    nothing in the response saying so — a wrong answer, not a cheaper one.
    """
    if not isinstance(query, dict):
        raise ValueError(f"invalid query: must be an object (got {type(query).__name__})")
    _scan_body(query)
    body = dict(query)
    for key, clamp in (("size", True), ("from", False)):
        if key in body:
            body[key] = _bounded_int(body[key], key=key, max_size=max_size, clamp=clamp)
    if "aggs" in body:
        body["aggs"] = _clamp_agg_sizes(body["aggs"], max_size=max_size)
    if "aggregations" in body:
        body["aggregations"] = _clamp_agg_sizes(body["aggregations"], max_size=max_size)
    return body


def _coerce_max_offset(raw: Any) -> int:
    """The remote `maxOffset`, as a non-negative int, or 0 when it is not usable.

    This value comes off the wire and then becomes a request parameter, so it is not
    assumed to be an int. `probe.get("maxOffset", 0) or 0` preserved any truthy
    non-integer, and the `<= 0` comparison that followed raised TypeError on a str.
    """
    if isinstance(raw, bool) or raw is None:
        return 0
    try:
        value = int(raw)
    except (TypeError, ValueError, OverflowError):
        # json.loads accepts the non-standard `Infinity` literal, and int(float('inf'))
        # raises OverflowError — neither TypeError nor ValueError, so it would escape.
        return 0
    return max(value, 0)


def _response_ceiling(max_content_bytes: int) -> int:
    """Byte ceiling for a content response carrying `max_content_bytes` of text.

    The cap counts characters of extracted text; the body is JSON, where UTF-8 encoding
    and escaping both expand it, plus the four other keys of the envelope. 4x + 8 KiB is
    the slack that buys — loose enough never to refuse an honest response, tight enough
    that an unbounded one still hits it.
    """
    return max_content_bytes * 4 + 8192


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
                "Authorization": f"Bearer {settings.api_key.get_secret_value()}",
                "X-DS-CSRF-TOKEN": csrf_token,
            },
            cookies={"_ds_csrf_token": csrf_token},
            event_hooks={"request": [read_only_hook(settings.url)]},
            timeout=settings.timeout_secs,
            # A CA bundle, when supplied, becomes the verification root — so a self-signed
            # or private-CA instance is trusted by evidence rather than by turning
            # verification off for every request that carries the bearer key. Built as an
            # explicit context because httpx deprecates `verify=<path string>`.
            verify=(
                ssl.create_default_context(cafile=settings.ca_bundle)
                if settings.ca_bundle
                else settings.verify_tls
            ),
        )

    async def aclose(self) -> None:
        await self._http.aclose()

    async def _fetch_json(
        self,
        method: str,
        url: str,
        *,
        context: str,
        resource: bool = False,
        params: dict[str, Any] | None = None,
        json_body: dict[str, Any] | None = None,
    ) -> Any:
        """Issue one request and decode its JSON, bounded in bytes and in wall time.

        Every Datashare call goes through here, which is the point: the byte ceiling used
        to live in `get_document_content` alone, and every endpoint added since — search,
        mapping, projects, the analysis helpers — silently did without it. A single choke
        point cannot be forgotten by the next endpoint.

        Streamed with a running counter, so an oversized body is abandoned mid-flight
        rather than materialised and then measured. `timeout_secs` is per-operation —
        httpx's read timeout bounds the wait for the *next chunk*, so a response dripping
        one byte every 29 seconds never trips it — hence the separate total deadline.
        """
        ceiling = _response_ceiling(self._settings.max_content_bytes)
        chunks: list[bytes] = []
        total = 0
        try:
            async with asyncio.timeout(self._settings.deadline_secs):
                async with self._http.stream(
                    method, url, params=params, json=json_body
                ) as resp:
                    declared = resp.headers.get("content-length")
                    if declared is not None and declared.isdigit() and int(declared) > ceiling:
                        raise ValueError(
                            f"{context}: response of {declared} bytes exceeds the "
                            f"{ceiling}-byte ceiling; request less — a smaller `size` or "
                            "`_source` for a search, a smaller offset/limit for content"
                        )
                    async for chunk in resp.aiter_bytes():
                        total += len(chunk)
                        if total > ceiling:
                            raise ValueError(
                                f"{context}: response exceeded the {ceiling}-byte ceiling "
                                "mid-stream; request less — a smaller `size` or `_source` "
                                "for a search, a smaller offset/limit for content"
                            )
                        chunks.append(chunk)
                    body = b"".join(chunks).decode("utf-8", "replace")
                    raise_for_status(resp, context=context, resource=resource, body=body)
        except TimeoutError as e:
            raise ValueError(
                f"{context}: the Datashare instance did not finish responding within "
                f"{self._settings.deadline_secs}s"
            ) from e
        try:
            return json.loads(body)
        except ValueError as e:
            raise ValueError(f"{context}: Datashare returned a body that is not JSON") from e

    async def list_projects(self) -> list[dict[str, Any]]:
        return cast(
            list[dict[str, Any]], await self._fetch_json("GET", "/api/project/", context="list_projects")
        )

    async def search(self, *, project: str, query: dict[str, Any]) -> dict[str, Any]:
        _validate_path_segment(project, field="project")
        body = _validate_search_body(query, max_size=self._settings.max_search_size)
        return cast(
            dict[str, Any],
            await self._fetch_json(
                "POST",
                f"/api/index/search/{project}/_search",
                context="search_documents",
                json_body=body,
            ),
        )

    async def get_document_metadata(
        self, *, project: str, doc_id: str, routing: str | None = None
    ) -> dict[str, Any]:
        _validate_path_segment(project, field="project")
        _validate_path_segment(doc_id, field="doc_id")
        params = {"routing": routing} if routing else None
        return cast(
            dict[str, Any],
            await self._fetch_json(
                "GET",
                f"/api/{project}/documents/{doc_id}",
                context="get_document_metadata",
                params=params,
            ),
        )

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
        if (offset is not None and offset < 0) or (limit is not None and limit < 0):
            # Same rule the search body gets. Without it a negative bound reaches
            # Datashare's Painless substring and comes back as an opaque upstream 500.
            raise ValueError("offset and limit must not be negative")
        cap = self._settings.max_content_bytes
        if offset is None and limit is None:
            # Full content. Datashare's no-range endpoint reads the whole text from
            # the relational DB, which is empty for index-only documents (CLI SCAN/INDEX
            # populates Elasticsearch, not the DB) and returns HTTP 500. Use the
            # Elasticsearch-backed ranged path instead: probe with limit=0 (a no-op
            # substring(0,0) that still reports `maxOffset`) to learn the length, then
            # fetch the range in one call — bounded by `cap`, because `maxOffset` is a
            # remote value derived from a document whose text this server did not author.
            probe = await self._get_content_range(
                project, doc_id, routing, 0, 0, target_language, resource
            )
            max_offset = _coerce_max_offset(probe.get("maxOffset"))
            if max_offset <= 0:
                return probe
            payload = await self._get_content_range(
                project, doc_id, routing, 0, min(max_offset, cap), target_language, resource
            )
            if max_offset > cap:
                payload["truncated"] = True
            return payload
        # Both supplied (the half-supplied and both-omitted cases returned above).
        assert offset is not None and limit is not None
        payload = await self._get_content_range(
            project, doc_id, routing, offset, min(limit, cap), target_language, resource
        )
        if limit > cap:
            payload["truncated"] = True
        return payload

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
        return cast(
            dict[str, Any],
            await self._fetch_json(
                "GET",
                f"/api/{project}/documents/content/{doc_id}",
                context="get_document_content",
                resource=resource,
                params=params,
            ),
        )

    async def get_mapping(self, *, project: str) -> dict[str, Any]:
        _validate_path_segment(project, field="project")
        return cast(
            dict[str, Any],
            await self._fetch_json(
                "GET",
                f"/api/index/search/{project}/_mapping",
                context=f"datashare://index/{project}/mapping",
                resource=True,
            ),
        )

    async def get_project_summary(self, *, project: str, format: str = "json") -> dict[str, Any]:
        """Generate comprehensive project summary combining all analyses."""
        _validate_path_segment(project, field="project")

        # `deadline_secs` bounds one HTTP request. This tool issues seven in sequence, so
        # without an outer budget one call could block for seven deadlines on a stdio
        # server the host has exactly one connection to.
        async with asyncio.timeout(self._settings.deadline_secs):
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

        result = await self._fetch_json(
            "POST",
            f"/api/index/search/{project}/_search",
            context="_analyze_document_structure",
            json_body=query,
        )

        # `_source` is absent from a hit when the query asked for none, and `path` is a
        # value extracted from the document, so neither its presence nor its type is this
        # server's to assume. A KeyError or TypeError here is not a ValueError and would
        # escape the tool handlers.
        paths = [
            hit.get("_source", {}).get("path", "")
            for hit in result.get("hits", {}).get("hits", [])
        ]

        tender_pattern = "/media/docs/"
        tender_ids = set()
        for path in paths:
            if isinstance(path, str) and tender_pattern in path:
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
            # creationDate is read out of the document file, so an absurd epoch reaches
            # here intact and makes fromtimestamp raise. The identical call in
            # _get_temporal_distribution is already wrapped; this one was missed, and an
            # unwrapped raise breaks the markdown summary permanently for that project.
            try:
                min_year = datetime.fromtimestamp(date_range["min"] / 1000, tz=UTC).year
                max_year = datetime.fromtimestamp(date_range["max"] / 1000, tz=UTC).year
            except (TypeError, ValueError, OSError, OverflowError):
                lines.append("- **Date Range:** unknown")
            else:
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

        result = await self._fetch_json(
            "POST",
            f"/api/index/search/{project}/_search",
            context="get_project_overview",
            json_body=query,
        )

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

        result = await self._fetch_json(
            "POST",
            f"/api/index/search/{project}/_search",
            context="get_document_type_distribution",
            json_body=query,
        )

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
        result = cast(
            dict[str, Any],
            await self._fetch_json(
                "POST",
                f"/api/index/search/{project}/_search",
                context="_get_total_count",
                json_body=query,
            ),
        )
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

        result = await self._fetch_json(
            "POST",
            f"/api/index/search/{project}/_search",
            context="get_temporal_distribution",
            json_body=query,
        )

        aggs = result.get("aggregations", {})
        buckets = aggs.get("by_year", {}).get("buckets", [])

        yearly_data = []
        for bucket in buckets:
            epoch_ms = bucket.get("key", 0)
            if epoch_ms and epoch_ms > 0:
                try:
                    year = datetime.fromtimestamp(epoch_ms / 1000, tz=UTC).year
                    yearly_data.append({"year": year, "count": bucket["doc_count"]})
                except (TypeError, ValueError, OSError, OverflowError, KeyError):
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
                        "note": (
                            f"Significant spike: {entry['count']} docs"
                            + (
                                f" ({round(entry['count'] / total_docs * 100, 1)}% of corpus)"
                                if total_docs > 0
                                else ""
                            )
                        ),
                    }
                )

        return {
            "totalDocuments": total_docs,
            "yearlyDistribution": yearly_data,
            "peaks": peaks,
            "medianCount": median_count,
        }
