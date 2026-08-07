# datashare-mcp

A read-only [MCP](https://modelcontextprotocol.io) server that wraps the
[ICIJ datashare](https://github.com/ICIJ/datashare) REST API so Claude (or any
MCP client) can list projects, run raw Elasticsearch DSL queries, and read
document metadata + extracted text from your datashare instance.

Single-user, stdio transport. v1 is read-only by design.

> Community project. Not affiliated with or endorsed by ICIJ.

## Install

```bash
uv tool install datashare-mcp
# or, ad-hoc, without installing:
uvx datashare-mcp
```

## Configure

Generate an API key on your datashare instance:

```bash
datashare api-key create <your-user>
```

Then set environment variables (in your MCP client's config, see below):

| Variable | Required | Default | Notes |
|---|---|---|---|
| `DATASHARE_URL` | yes | — | e.g. `http://localhost:8888`. Trailing slashes are stripped. |
| `DATASHARE_API_KEY` | yes | — | Bearer key from `datashare api-key create`. |
| `DATASHARE_TIMEOUT_SECS` | no | 30 | Per-request HTTP timeout. |
| `DATASHARE_VERIFY_TLS` | no | true | Set `false` for self-signed dev certificates. |

## Wire to Claude Code

Add to `~/.claude.json` or the project's `.mcp.json`:

```json
{
  "mcpServers": {
    "datashare": {
      "command": "datashare-mcp",
      "env": {
        "DATASHARE_URL": "http://localhost:8888",
        "DATASHARE_API_KEY": "ds_..."
      }
    }
  }
}
```

## Wire to Claude Desktop (Cowork)

Edit `~/Library/Application Support/Claude/claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "datashare": {
      "command": "/Users/<you>/.local/bin/datashare-mcp",
      "env": {
        "DATASHARE_URL": "http://localhost:8888",
        "DATASHARE_API_KEY": "ds_..."
      }
    }
  }
}
```

Then **quit Claude.app fully (Cmd-Q) and relaunch** -- closing the window keeps the MCP subprocess alive.

## Local environment

A local Datashare to develop and test against is a Docker Compose project:
`docker compose up -d` from the repository root. The topology, corpus ingest, project
registration, API-key handling and troubleshooting are documented in
[`docs/local-environment.md`](docs/local-environment.md).

## What Claude can do

**Tools (8):**

- `list_projects()` — discover the projects on this instance.
- `get_project_overview(project)` — document count, language distribution and creation-date range.
- `get_document_type_distribution(project)` — content types with counts and percentages.
- `get_temporal_distribution(project)` — document counts by year, with peak detection.
- `get_project_summary(project, format?)` — the three analyses above combined, as JSON or Markdown.
- `search_documents(project, query)` — raw Elasticsearch DSL passthrough.
- `get_document_metadata(project, doc_id, routing?)` — path, contentType, language, …
- `get_document_content(project, doc_id, routing?, offset?, limit?, target_language?)` — extracted text.

The four analytics tools are aggregations over the same `_search` endpoint that backs
`search_documents`, not additional Datashare endpoints.

**Resources (3):**

- `datashare://projects` — same as `list_projects` but as a resource.
- `datashare://index/{project}/mapping` — Elasticsearch mapping for the project. Read this first to write good queries.
- `datashare://document/{project}/{doc_id}` — full extracted text for inline citation.

## Development

```bash
git clone https://github.com/sapran/datashare-mcp
cd datashare-mcp
uv sync
uv run pytest          # mocked unit tests
uv run ruff check .
```

To run the gated live suite:

```bash
DATASHARE_URL=... DATASHARE_API_KEY=... DATASHARE_LIVE_TESTS=1 \
  DATASHARE_LIVE_PROJECT=<your-project> uv run pytest tests/live
```

## License

MIT.
