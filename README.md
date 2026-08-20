# datashare-mcp

A **read-only** [MCP](https://modelcontextprotocol.io) server over the
[ICIJ Datashare](https://github.com/ICIJ/datashare) HTTP API, so an LLM agent can search a
document corpus with raw Elasticsearch DSL, size it up, and read extracted text — without
being able to change it.

> Community project. Not affiliated with or endorsed by ICIJ.

## Why read-only, and why the credential cannot be the boundary

Aleph has read-only API roles; Datashare, in the mode this server is deployed against,
does not. `--mode=LOCAL` binds `CsrfFilter` and `LocalUserFilter` and never
`ApiKeyFilter` (`ServerMode` is the one that adds it), so "use a read-only key" is not an
available defence — the key is not even checked. And Datashare's write surface sits on the
*same* `/api/index` prefix as the two endpoints this server legitimately calls:
`PUT /api/index/:index`, `POST /api/index/:index/_close` (takes the corpus offline),
`PUT|DELETE /api/index/_snapshot/...`, `POST .../_restore`. All but one are guarded only by
`checkAllowedMode(LOCAL, EMBEDDED)` — which admits LOCAL; `createIndex` has no mode check
at all.

So the boundary is in this process, as a fixed allowlist of five `(method, path)` pairs
enforced on every outgoing httpx request, redirect hops included
([`src/datashare_mcp/readonly.py`](src/datashare_mcp/readonly.py)):

```
GET   /api/project
POST  /api/index/search/{project}/_search
GET   /api/index/search/{project}/_mapping
GET   /api/{project}/documents/{doc_id}
GET   /api/{project}/documents/content/{doc_id}
```

Extending that tuple is the only way to widen the surface: no tool argument, caller value
or redirect can. The pins are load-bearing rather than decorative, because Datashare's own
`IndexAccessVerifier.checkPath` forwards *any* path under a granted index once the method
is GET ("As it is a GET method, all paths are accepted"), and short-circuits entirely on a
`_search/scroll` prefix before any grant check.

### What the allowlist is worth, and what it is not

Nothing in a stock local Datashare authenticates anything. `--mode=LOCAL` binds
`CsrfFilter` and `LocalUserFilter` and never `ApiKeyFilter`, and the Elasticsearch behind
it is commonly run with `xpack.security.enabled=false`. The bearer key this server sends
on every request is therefore **decorative against such an instance** — it is checked by a
SERVER-mode Datashare, not by that one. The only control in place is that both ports are
published on loopback.

So be precise about what the allowlist buys:

| | Reaches the corpus | Constrained by the allowlist |
|---|---|---|
| A tool call through `datashare-mcp` | yes | **yes** — five read endpoints, nothing else |
| `curl http://localhost:8888/api/...` | yes, read **and write** | no |
| `curl -XPOST http://localhost:9201/<index>/_delete_by_query` | yes, destroys the index | no |

The allowlist is an **in-client control**: it bounds what this server process sends. It is
not a sandbox around Datashare. An agent session that holds this MCP server *and* a shell
or a generic HTTP tool has an unauthenticated write path to the corpus that the allowlist
never sees. What the allowlist does buy is that a compromised or mistaken caller of the
MCP server — including a corpus document attempting an injection — cannot turn the server
itself into that write path.

If that is not good enough for a given corpus, the fix is server-side, not in this client:
run Datashare with `--mode=SERVER` so `ApiKeyFilter` is bound (which also makes the key
meaningful), and set `xpack.security.enabled=true` with a password supplied from the
environment. Both change how the whole stack is operated — the UI starts demanding
authentication, and ingest and API-key creation need credentials — so it is a deliberate
switch, not a default.

## Install

Requires Python ≥ 3.12 and [`uv`](https://github.com/astral-sh/uv).

```bash
# Pin the exact version: this server is handed your Datashare key, and an unpinned spec
# resolves to whatever happens to be newest at launch.
uv tool install datashare-mcp==0.3.0

# Or, from a checkout:
uv sync --all-extras
```

PyPI refuses to re-upload a version that already exists, so `==0.3.0` names one immutable
artifact — the same guarantee a commit SHA gives — and it additionally carries a PEP 740
attestation naming the workflow and commit that built it. Keep the `==` pin on every path
below.

## Configure

Generate an API key on your Datashare instance:

```bash
datashare api-key create <your-user>
```

| Variable | Required | Default | Notes |
| --- | --- | --- | --- |
| `DATASHARE_URL` | yes | — | Instance root, e.g. `http://localhost:8888`. Trailing slashes are stripped. |
| `DATASHARE_API_KEY` | yes | — | Bearer key from `datashare api-key create`. |
| `DATASHARE_TIMEOUT_SECS` | no | `30` | Per-operation HTTP timeout (connect, read, write, pool). |
| `DATASHARE_DEADLINE_SECS` | no | `120` | Total wall-clock budget for one call. The read timeout bounds the wait for the *next chunk*, so a slow drip needs this too. |
| `DATASHARE_MAX_SEARCH_SIZE` | no | `200` | Clamp applied to a search body's `size`/`from`, and to every `size` nested in `aggs`. |
| `DATASHARE_MAX_CONTENT_BYTES` | no | `1000000` | Ceiling on extracted text fetched in one call; larger documents come back `truncated`. Also sets the response byte ceiling every endpoint is streamed against. |
| `DATASHARE_CA_BUNDLE` | no | — | PEM CA bundle, checked for existence at startup. Use this for a self-signed or private-CA instance — verification stays *on*, against your CA. |
| `DATASHARE_VERIFY_TLS` | no | `true` | `false` disables certificate validation for every request, and every request carries the bearer key. Loopback or an otherwise trusted link only; on a remote instance set `DATASHARE_CA_BUNDLE` instead. |

Missing required variables fail at startup with a message on stderr, visible in the MCP
client's logs.

### Where to put them

Both required variables must reach the server's process environment; the server reads
nothing else.

- **omp** autoloads `.env` into its own environment at startup, and an stdio MCP child
  inherits it. Precedence, highest first: inherited process environment → `<cwd>/.env` →
  `~/.omp/agent/.env` → `~/.omp/.env` → `~/.env`; a variable already set is never
  overwritten by a later file. Put the lines in `~/.omp/.env` for every project, or
  `<project>/.env` for one, then `chmod 600` the file.
- **Every other harness** — Claude Code, Claude Desktop, opencode — needs them exported
  from your shell rc (`~/.zshrc`), or set in an `env` block on that client's own MCP entry,
  accepting that the literal value then lives in that config file.
- **macOS Keychain** avoids the literal value entirely; the shipped plugin reads it this
  way:

  ```bash
  security add-generic-password -U -a "$USER" -s datashare-mcp -w   # paste at the prompt
  export DATASHARE_API_KEY="$(security find-generic-password -s datashare-mcp -a "$USER" -w)"
  ```

Never commit the key: `.gitignore` already lists `.env`.

## Wire it up

### omp and Claude Code (plugin)

This repository is itself a plugin marketplace, so one install delivers the server and the
`datashare-corpus` method skill together:

```bash
omp plugin marketplace add sapran/datashare-mcp
omp plugin install datashare@datashare-mcp
```

Claude Code: `/plugin marketplace add sapran/datashare-mcp`, then
`/plugin install datashare@datashare-mcp`.

The plugin namespaces its server as `datashare:mcp`, so tools reach the model as
`mcp__datashare_mcp_<tool>` — e.g. `mcp__datashare_mcp_search_documents`. Its `.mcp.json`
carries no literal secret: `DATASHARE_URL` falls back to `http://localhost:8888` and
`DATASHARE_API_KEY` is read from the login Keychain, falling back to the environment
variable. Credentials, pinning, two instances at once and running from a checkout:
[`plugins/datashare/README.md`](plugins/datashare/README.md).

### Any stdio MCP client (`mcp.json`)

```json
{
  "mcpServers": {
    "datashare": {
      "type": "stdio",
      "command": "uvx",
      "args": ["--from", "datashare-mcp==0.3.0", "datashare-mcp"]
    }
  }
}
```

No `env` block — credentials come from the environment the client itself runs in. Under omp
this form is not plugin-namespaced, so its tools are `mcp__datashare_<tool>`. Keep the `==`
pin: an unpinned spec resolves to whatever is newest at launch, in a process you have just
handed your Datashare key.

### Claude Desktop

`~/Library/Application Support/Claude/claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "datashare": {
      "command": "/Users/<you>/.local/bin/uvx",
      "args": ["--from", "datashare-mcp==0.3.0", "datashare-mcp"],
      "env": {
        "DATASHARE_URL": "http://localhost:8888",
        "DATASHARE_API_KEY": "<your key>"
      }
    }
  }
}
```

**Use an absolute path for `command`.** Claude Desktop launches MCP servers from launchd
with a minimal `PATH` (`/usr/bin:/bin:/usr/sbin:/sbin`) and never reads your shell rc, so a
bare `uvx` dies with `spawn uvx ENOENT` and the server simply never appears. Run
`which uvx` and paste the result — commonly `~/.local/bin/uvx` from the uv installer or
`/opt/homebrew/bin/uvx` under Homebrew. The same applies to `datashare-mcp` itself if you
installed it with `uv tool install`.

Claude Desktop also expands no shell variables, so the key is a literal in this file —
`chmod 600` it, or install the plugin instead, which reads the key from the login Keychain.

Then **quit Claude.app fully (Cmd-Q) and relaunch** — closing the window leaves the MCP
subprocess alive with the old config.

### opencode

`~/.config/opencode/opencode.json`:

```json
{
  "$schema": "https://opencode.ai/config.json",
  "mcp": {
    "datashare": {
      "type": "local",
      "command": ["uvx", "--from", "datashare-mcp==0.3.0", "datashare-mcp"],
      "enabled": true,
      "environment": {
        "DATASHARE_URL": "http://localhost:8888",
        "DATASHARE_API_KEY": "{env:DATASHARE_API_KEY}"
      }
    }
  }
}
```

Tools then appear to the model as `datashare_search_documents`, `datashare_list_projects`,
and so on (`sanitize(server) + "_" + sanitize(tool)`).

## Surface

Eight tools, all reads:

| Tool | Datashare endpoint | Purpose |
| --- | --- | --- |
| `list_projects` | `GET /api/project` | which projects this instance holds; call it first |
| `get_project_overview` | `POST .../_search` | document count, language distribution, creation-date range |
| `get_document_type_distribution` | `POST .../_search` | content types with counts and percentages, related types grouped |
| `get_temporal_distribution` | `POST .../_search` | documents by year, flagging years above 2× the median |
| `get_project_summary` | `POST .../_search` | the three analyses plus path structure, data quality and insights; `format="markdown"` for prose |
| `search_documents` | `POST /api/index/search/{project}/_search` | raw Elasticsearch DSL passthrough, the main entry point |
| `get_document_metadata` | `GET /api/{project}/documents/{id}` | path, contentType, language, contentLength, tags |
| `get_document_content` | `GET /api/{project}/documents/content/{id}` | extracted text, optionally a byte range or a translation |

The four analytics tools are aggregations over the same `_search` endpoint that backs
`search_documents`, not additional Datashare endpoints.

Three resources: `datashare://projects`, `datashare://index/{project}/mapping` (read this
before composing queries — it names every field, type and analyzer, NamedEntity fields
included), and `datashare://document/{project}/{doc_id}` (full extracted text, no slicing;
for large documents use the tool with `offset`/`limit` instead).

### Design choices worth knowing

- **Raw ES DSL is exposed deliberately.** Datashare's search endpoint is an Elasticsearch
  proxy, and its own javadoc says "everything sent is forwarded". Rather than invent a
  query grammar, the server passes the body through and points the agent at the mapping
  resource first. Precision comes from reading the mapping, not from guessing field names.
- **The path pin does not look at the body, so the body has its own rules.** A search body
  is refused when it carries server-side scripting — matched at a token boundary,
  `(?:^|_)script`, because an earlier enumerated list missed `scripted_metric`, whose
  Painless hides under `init_script`/`map_script`/`combine_script`/`reduce_script` — or
  `runtime_mappings`, or a cross-index document reference matched by *shape*
  (`{index, id}`, `{_index, _id}`, `{index, id, path, routing}`), which covers the `terms`
  lookup, `geo_shape.indexed_shape`, `percolate`, `pinned.docs` and `more_like_this`
  alike. Recursion is capped at depth 32; an unbounded walk on caller JSON is its own DoS.
- **`size` and `from` are clamped, including inside `aggs`.** A model asking for 10 000 hits
  gets `DATASHARE_MAX_SEARCH_SIZE`, not a refusal and not an oversized response.
- **Scroll is unreachable.** `_search/scroll` matches no allowlist pair, which is also the
  shape that short-circuits Datashare's own grant check. Page with `from`/`size`, or
  narrow with aggregations.
- **One bounded fetch, not one per endpoint.** Every call goes through a single
  `_fetch_json` that streams with a running byte counter and abandons an oversized body
  mid-flight, under a total `asyncio.timeout` deadline separate from the per-chunk HTTP
  timeout. The ceiling used to live in `get_document_content` alone, and each endpoint
  added afterwards silently did without it.
- **Corpus text is fenced as untrusted data.** Document text and search-hit `content` come
  back inside a nonce-bound frame, and every corpus-derived structure carries a `_notice`
  saying it is evidence to quote and cite, never instructions. The nonce is per-call and
  unpredictable, so a document that contains a closing delimiter of its own cannot
  impersonate the harness — the reader can tell which marker the server wrote. Datashare
  indexes Tika metadata under key names taken from the file itself, so a document really
  can carry its own `_notice`.
- **Error text is masked by default.** `mask_error_details=True`, with deliberate
  `ToolError`/`ResourceError` messages passed through: 401 names the key variable, 404
  suggests `list_projects`, 400 shows the Elasticsearch `error.type` token and nothing
  else. A transport failure says the instance is unreachable rather than echoing internals.
- **Project names are validated against the guard's own charset.** `client.py` builds its
  validator from the same `_SEGMENT` pattern the allowlist uses, so the two cannot drift;
  `_all`, `_any` and `_none` are refused because Elasticsearch reads them as "every index".

## Local environment

A local Datashare to develop and test against is a Docker Compose project. First run, from
the repository root:

```bash
# 1. Configuration. DATASHARE_DATA_DIR is required and must be an absolute path to a
#    directory of documents; it is mounted read-only.
cp env.example .env

# 2. Create the two data volumes. They are declared `external` so that no compose command
#    can delete them — which also means compose will not create them for you, and `up`
#    fails with `external volume "datashare-es-data" not found` until you do.
docker volume create datashare-es-data
docker volume create datashare-dist

# 3. Start.
docker compose up -d

# 4. Index the corpus. Stop the app first — the one-shots share its SQLite file and two
#    processes must never write it at once. The CLI JVM may hang after "exiting main";
#    once "drained N documents" appears, it is done.
docker compose stop app
docker compose --profile tools run --rm ingest

# 5. Mint an API key. Datashare cannot show you an existing one, only mint a new one, so
#    this invalidates any key already in use. It is printed in cleartext.
docker compose --profile tools run --rm apikey
docker compose start app
```

CLI ingest creates the Elasticsearch index but **not** the relational project row, and
without that row `/api/index/...` answers 401 and `list_projects` comes back empty. Register
it once, with the key from step 5:

```bash
KEY=<the key printed in step 5>
curl -s -H "Authorization: Bearer $KEY" -H "X-DS-CSRF-TOKEN: x" -b "_ds_csrf_token=x" \
  -H "Content-Type: application/json" -X POST http://localhost:8888/api/project/ \
  -d '{"name":"demo","label":"demo","sourcePath":"/home/datashare/data"}'

# Verify:
curl -s -H "Authorization: Bearer $KEY" http://localhost:8888/api/project/
```

The name must match `DATASHARE_PROJECT` in `.env`. [`docker-compose.yml`](docker-compose.yml)
documents the topology, the volume handling and both one-shots inline; the trust boundary is
described above. Note that the published image serves the REST API but not the web UI, so
`http://localhost:8888` in a browser will 404.

## Develop

```bash
uv sync --all-extras
uv run pytest
uv run ruff check .
uv run mypy
```

190 unit tests mock all HTTP with `respx`. Four tests under `tests/live/` hit a real
instance and are skipped unless `DATASHARE_LIVE_TESTS=1`:

```bash
DATASHARE_URL=... DATASHARE_API_KEY=... DATASHARE_LIVE_TESTS=1 \
  DATASHARE_LIVE_PROJECT=<your-project> uv run pytest tests/live
```

They assert shape and contract, not any instance's content. One of them exists because the
corresponding bug survived a fully green mocked suite: `get_document_content` with no
offset/limit hit Datashare's relational-DB path, which is empty for index-only documents
and answers HTTP 500 — the client now probes `maxOffset` and fetches the
Elasticsearch-backed range instead. Run the live suite once against a new instance before
trusting the server there.

CI runs lint, `mypy --strict` and the mocked suite on Python 3.12 and 3.13; every workflow
action is pinned to a full commit SHA.

## License

MIT
