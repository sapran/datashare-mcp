from __future__ import annotations

from typing import Any, cast

from fastmcp import FastMCP
from fastmcp.exceptions import ToolError

from .client import DatashareClient
from .config import Settings


def build_server(settings: Settings) -> tuple[FastMCP, DatashareClient]:
    """Construct a configured FastMCP server and its DatashareClient.

    Returned together so tests (and the entry point) can manage the client lifetime.
    """
    mcp = FastMCP(
        name="datashare-mcp",
        instructions=(
            "Wraps a single datashare instance. Tools are read-only. "
            "Use list_projects first to discover available projects, then "
            "search_documents with raw Elasticsearch DSL. The mapping is "
            "available as a resource at datashare://index/{project}/mapping."
        ),
    )
    client = DatashareClient(settings)

    @mcp.tool
    async def list_projects() -> list[dict[str, Any]]:
        """List all projects available on this datashare instance.

        Returns a list of project objects (name, sourcePath, label, description, sourceUrl, ...).
        Call this first to learn which project names are valid for the other tools.
        """
        return await client.list_projects()

    @mcp.tool
    async def get_project_overview(project: str) -> dict[str, Any]:
        """Get high-level statistics about a Datashare project.

        Returns total document count, language distribution, and date range.
        Use this as the starting point for project summarization.
        """
        try:
            return await client.get_project_overview(project=project)
        except ValueError as e:
            raise ToolError(str(e)) from e

    @mcp.tool
    async def get_document_type_distribution(project: str) -> dict[str, Any]:
        """Analyze document content types with counts and percentages.

        Groups related types (e.g., DOC + DOCX as "Word Documents").
        Returns distribution sorted by count descending.
        """
        try:
            return await client.get_document_type_distribution(project=project)
        except ValueError as e:
            raise ToolError(str(e)) from e

    @mcp.tool
    async def get_temporal_distribution(project: str) -> dict[str, Any]:
        """Get document counts grouped by year with peak detection.

        Returns yearly distribution and highlights significant spikes
        (years with >2x median document count).
        """
        try:
            return await client.get_temporal_distribution(project=project)
        except ValueError as e:
            raise ToolError(str(e)) from e

    @mcp.tool
    async def get_project_summary(project: str, format: str = "json") -> dict[str, Any]:
        """Generate comprehensive project summary.

        Combines all analyses into a single summary including:
        - Document counts and language distribution
        - Document type breakdown with grouping
        - Temporal distribution with peak detection
        - Document structure analysis
        - Data quality assessment
        - Automatic insights and suggestions

        Args:
            project: Project name
            format: "json" (default) or "markdown"
        """
        try:
            return await client.get_project_summary(project=project, format=format)
        except ValueError as e:
            raise ToolError(str(e)) from e

    @mcp.tool
    async def search_documents(project: str, query: dict[str, Any]) -> dict[str, Any]:
        """Run a raw Elasticsearch DSL query against a project's index.

        `query` is a complete Elasticsearch request body — for example
        {"query": {"match": {"content": "kremlin"}}, "size": 10, "from": 0}.
        Read the schema from the resource datashare://index/{project}/mapping
        before constructing complex queries.

        Returns the raw Elasticsearch response (hits, aggregations, total).
        """
        try:
            return await client.search(project=project, query=query)
        except ValueError as e:
            raise ToolError(str(e)) from e

    @mcp.tool
    async def get_document_metadata(
        project: str, doc_id: str, routing: str | None = None
    ) -> dict[str, Any]:
        """Fetch metadata for one document: path, contentType, language, contentLength, tags, etc.

        `routing` is required for child documents (e.g., embedded files inside a parent ZIP);
        leave it None for top-level documents.
        """
        try:
            return await client.get_document_metadata(
                project=project, doc_id=doc_id, routing=routing
            )
        except ValueError as e:
            raise ToolError(str(e)) from e

    @mcp.tool
    async def get_document_content(
        project: str,
        doc_id: str,
        routing: str | None = None,
        offset: int | None = None,
        limit: int | None = None,
        target_language: str | None = None,
    ) -> dict[str, Any]:
        """Fetch extracted text as JSON {content, maxOffset, offset, limit, targetLanguage}.

        `offset` and `limit` are byte offsets into the extracted text and must be
        supplied together (or both omitted to return the full content).
        Use `target_language` (e.g. "ENGLISH") to request a translated slice.
        """
        try:
            return await client.get_document_content(
                project=project,
                doc_id=doc_id,
                routing=routing,
                offset=offset,
                limit=limit,
                target_language=target_language,
            )
        except ValueError as e:
            raise ToolError(str(e)) from e

    @mcp.resource("datashare://projects", mime_type="application/json")
    async def projects_resource() -> list[dict[str, Any]]:
        """Browsable list of projects (mirrors list_projects)."""
        return await client.list_projects()

    @mcp.resource("datashare://index/{project}/mapping", mime_type="application/json")
    async def mapping_resource(project: str) -> dict[str, Any]:
        """Elasticsearch mapping for a project's index.

        Read this before composing search_documents queries — it lists every
        field, type, and analyzer the index supports (including NamedEntity fields).
        """
        return await client.get_mapping(project=project)

    @mcp.resource("datashare://document/{project}/{doc_id}", mime_type="text/plain")
    async def document_resource(project: str, doc_id: str) -> str:
        """Full extracted text of a document (no byte-range slicing).

        For documents larger than a few hundred KB, prefer the
        get_document_content tool with offset/limit instead.
        """
        payload = await client.get_document_content(project=project, doc_id=doc_id, resource=True)
        return cast(str, payload.get("content", ""))

    return mcp, client
