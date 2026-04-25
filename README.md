# datashare-mcp

A read-only [MCP](https://modelcontextprotocol.io) server that wraps the
[ICIJ datashare](https://github.com/ICIJ/datashare) REST API so Claude (or any
MCP client) can list projects, run raw Elasticsearch DSL queries, and read
document metadata + extracted text from your datashare instance.

Single-user, stdio transport. v1 is read-only by design.

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
| `DATASHARE_URL` | yes | — | e.g. `http://localhost:8080`. Trailing slashes are stripped. |
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
        "DATASHARE_URL": "http://localhost:8080",
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
        "DATASHARE_URL": "http://localhost:8080",
        "DATASHARE_API_KEY": "ds_..."
      }
    }
  }
}
```

Then **quit Claude.app fully (Cmd-Q) and relaunch** -- closing the window keeps the MCP subprocess alive.

## End-to-end local setup

A from-scratch walkthrough for running datashare on macOS, indexing some files, and chatting about them in Claude Desktop's Cowork. Everything lives under `~/datashare/`.

### Prerequisites

Docker Desktop, JDK 21, Maven 3.8+, Node >=20, Yarn 1.22+, [`uv`](https://docs.astral.sh/uv/), `git`.

### 1. Clone the repos

```bash
mkdir -p ~/datashare && cd ~/datashare
git clone https://github.com/ICIJ/datashare.git
git clone https://github.com/ICIJ/datashare-client.git
git clone https://github.com/sapran/datashare-mcp.git
mkdir -p ~/datashare/data
```

Final layout:

```
~/datashare/
|-- datashare/          # Java backend
|-- datashare-client/   # Vue 3 frontend
|-- datashare-mcp/      # this MCP server
\-- data/               # documents to ingest
```

### 2. Install and run datashare

```bash
cd ~/datashare/datashare
make devenv                       # writes datashare-devenv.properties
docker compose up -d              # Postgres, ES, Redis, AMQP, S3-mock, Temporal
make install                      # liquibase migrate + jOOQ codegen + mvn build
./run.sh --mode LOCAL --dataDir ~/datashare/data
```

Frontend dev server (separate terminal):

```bash
cd ~/datashare/datashare-client
yarn install --frozen-lockfile
yarn serve                        # http://localhost:9009 -> proxies /api to :8080
```

Smoke test: `curl -s localhost:8080/version | jq` and open http://localhost:9009.

### 3. Add data to the project

Default project in LOCAL mode is `local-datashare`.

```bash
cp ~/path/to/files/*.{pdf,docx,txt} ~/datashare/data/
```

In the UI -> **Tasks -> Analyze**, run **SCAN -> INDEX** on `local-datashare`. Or one-shot from CLI:

```bash
cd ~/datashare/datashare
./run.sh --mode LOCAL --dataDir ~/datashare/data --stages "SCAN,INDEX"
```

Verify: `curl -s 'localhost:9200/local-datashare/_count' | jq`.

### 4. Install datashare-mcp and generate an API key

```bash
cd ~/datashare/datashare-mcp
uv tool install --from . datashare-mcp        # -> ~/.local/bin/datashare-mcp
```

Generate an API key against the **running** backend -- the `--dataSourceUrl` override is mandatory because the launcher's default points elsewhere:

```bash
cd ~/datashare/datashare
./run.sh api-key create local \
  --dataSourceUrl "jdbc:sqlite:file:$(pwd)/dist/datashare.db"
# look for: "generated secret key for user local ... <KEY>"   (shown once)
```

### 5. Wire into Claude Desktop and chat

Drop the key into `claude_desktop_config.json` (see "Wire to Claude Desktop (Cowork)" above), Cmd-Q + relaunch Claude.app, then in a Cowork project ask:

- "List datashare projects."
- "Search `local-datashare` for documents mentioning *budget* -- top 10 hits, show titles."
- "Get metadata for document `<doc_id>`."
- "Read content of `<doc_id>` from offset 0 limit 4000."
- "Pull the index mapping for `local-datashare`."

### Troubleshooting

| Symptom | Cause / fix |
|---|---|
| `api-key create` -> `SQLITE_CANTOPEN`, then "Unsupported class file major version 65" | Wrong DB path. Pass `--dataSourceUrl "jdbc:sqlite:file:$(pwd)/dist/datashare.db"`. The ASM/Guice "major version 65" line is downstream noise. |
| Direct API call -> 403 `"CSRF token wrong or missing"` | Non-GET `/api/*` needs matching `Cookie: _ds_csrf_token=X` + `X-DS-CSRF-TOKEN: X` (any matching pair). The MCP client sends these automatically. |
| MCP edits not taking effect in Cowork | Quit Claude.app with Cmd-Q, then relaunch. Closing the window keeps the subprocess. |
| Search returns 0 hits | An INDEX stage didn't run. SCAN alone only enumerates files. |
| Port 8080 in use | Frontend `.env` proxy is hardcoded to it; either free the port or edit `datashare-client/.env`. |

## What Claude can do

**Tools (4):**

- `list_projects()` — discover the projects on this instance.
- `search_documents(project, query)` — raw Elasticsearch DSL passthrough.
- `get_document_metadata(project, doc_id, routing?)` — path, contentType, language, …
- `get_document_content(project, doc_id, routing?, offset?, limit?, target_language?)` — extracted text.

**Resources (3):**

- `datashare://projects` — same as `list_projects` but as a resource.
- `datashare://index/{project}/mapping` — Elasticsearch mapping for the project. Read this first to write good queries.
- `datashare://document/{project}/{id}` — full extracted text for inline citation.

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
