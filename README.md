# datashare-mcp

A **read-only** [MCP](https://modelcontextprotocol.io) server over the
[ICIJ Datashare](https://github.com/ICIJ/datashare) HTTP API, so an LLM agent can search a
document corpus with raw Elasticsearch DSL, size it up, and read extracted text — without
being able to change it.

Sibling tools: [`aleph-mcp`](https://github.com/sapran/aleph-mcp) (the same idea for
OCCRP Aleph) and [`aleph-coldbackup`](https://github.com/sapran/aleph-coldbackup) (bulk
export of an Aleph collection).

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

> **Scope.** This is an in-client control. It bounds what *this process* sends and nothing
> else. A LOCAL-mode Datashare with `xpack.security.enabled=false` is unauthenticated to
> everything else on the host, so any other tool in the same session that can make an HTTP
> request — a shell, a generic fetch tool — still has full read *and write* access to
> `127.0.0.1:8888` and `127.0.0.1:9201`, and the allowlist never sees it. See "Trust
> boundary" in [`docs/local-environment.md`](docs/local-environment.md).

## Install

Requires Python ≥ 3.12 and [`uv`](https://github.com/astral-sh/uv).

```bash
# Pin a reviewed commit: this server is handed your Datashare key, and an unpinned git+
# spec builds and runs whatever the branch head happens to be. Current version: 0.2.0.
uv tool install git+https://github.com/sapran/datashare-mcp.git@<commit-sha>

# Or, from a checkout:
uv sync --all-extras
```

Not published to PyPI yet, so a bare `uvx datashare-mcp` resolves nothing; the
`--from git+…` spec is required on every path below.

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
      "args": ["--from", "git+https://github.com/sapran/datashare-mcp.git@<commit-sha>", "datashare-mcp"]
    }
  }
}
```

No `env` block — credentials come from the environment the client itself runs in. Under omp
this form is not plugin-namespaced, so its tools are `mcp__datashare_<tool>`. Pin
`@<commit-sha>` to a full commit: an unpinned `git+` spec builds and runs whatever the
branch head is at launch, in a process you have just handed your Datashare key. A tag is
mutable and is not an acceptable substitute.

### Claude Desktop

`~/Library/Application Support/Claude/claude_desktop_config.json`:

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
      "command": ["uvx", "--from", "git+https://github.com/sapran/datashare-mcp.git@<commit-sha>", "datashare-mcp"],
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

A local Datashare to develop and test against is a Docker Compose project:
`docker compose up -d` from the repository root. Topology, corpus ingest, project
registration, API-key handling, the trust boundary and troubleshooting are documented in
[`docs/local-environment.md`](docs/local-environment.md).

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
