# `datashare` plugin

Installs the [`datashare-mcp`](https://github.com/sapran/datashare-mcp) read-only MCP
server, and the method skill that tells an agent how to work a Datashare corpus — read the
mapping and size the corpus before querying it, rather than guessing at field names.

## What it installs

- **One MCP server**, `datashare:mcp` — 8 read tools and 3 resources
  (`datashare://projects`, `datashare://index/{project}/mapping`,
  `datashare://document/{project}/{doc_id}`).
- **One skill**, `datashare-corpus` — mapping-first querying, the analytics tools that
  size and shape a corpus before searching it, `_routing` propagation, and the paging rule
  for large documents.

Under omp the plugin's server is namespaced `<plugin>:<server key>` = `datashare:mcp`, and
tool names are `mcp__<sanitized server>_<tool>`. So the model sees
**`mcp__datashare_mcp_<tool>`** — for example `mcp__datashare_mcp_search_documents`.

## Prerequisites

- [`uv`](https://github.com/astral-sh/uv) on `PATH` (it provides `uvx`). Python ≥ 3.12 is
  fetched by `uv` itself.
- A reachable Datashare instance and an API key for it. The repository's
  [`docker-compose.yml`](../../docker-compose.yml) brings up the local deployment this
  plugin defaults to.

The server is read-only whatever the key permits: every outgoing request is matched
against a fixed allowlist in `src/datashare_mcp/readonly.py` and refused before it is
sent. That matters here because Datashare's `LOCAL` mode does not enforce API-key
authentication at all, so a read-only credential is not an available boundary.

Read that as scoped: the allowlist bounds what *this server* sends, and a LOCAL-mode
instance is unauthenticated to everything else on the host. Anything else in the same
session that can make an HTTP request — a shell, a fetch tool — still has full read and
write access to the corpus, and the allowlist never sees it. See "What the allowlist is
worth, and what it is not" in the [repository README](../../README.md).

## Install (omp)

```bash
omp plugin marketplace add sapran/datashare-mcp
omp plugin install datashare@datashare-mcp
```

Marketplace mutations made from the TUI or CLI do not refresh a live session: run
`/reload-plugins` to pick up the skill and the MCP server, or restart omp.

For a single repository instead of your user scope, add `--scope project`:

```bash
omp plugin install datashare@datashare-mcp --scope project
```

## Install (Claude Code)

```
/plugin marketplace add sapran/datashare-mcp
/plugin install datashare@datashare-mcp
```

The catalog lives at `.claude-plugin/marketplace.json`, which both harnesses read.

## Where the URL and key go

Two variables:

| Variable | Notes |
| --- | --- |
| `DATASHARE_URL` | Instance root, e.g. `http://localhost:8888`. Trailing slashes are stripped. **Defaults to `http://localhost:8888`** if unset. |
| `DATASHARE_API_KEY` | Bearer key. Read from the login Keychain first; falls back to the environment variable. |

Optional: `DATASHARE_TIMEOUT_SECS` (default `30`), `DATASHARE_DEADLINE_SECS` (default
`120`), `DATASHARE_CA_BUNDLE` (unset), `DATASHARE_VERIFY_TLS` (default `true`).

On TLS, in order of preference:

1. A publicly-trusted certificate — nothing to configure.
2. A self-signed or private-CA instance: point `DATASHARE_CA_BUNDLE` at the PEM bundle.
   Verification stays on, against your CA. The path is checked at startup, so a typo
   fails immediately instead of on the first request.
3. `DATASHARE_VERIFY_TLS=false` **only** for loopback or an otherwise trusted link. It
   disables certificate validation for *every* request, and every request carries the
   bearer key — on a remote `https://` instance that offers the key to anyone positioned
   on the path. Use option 2 there instead.

The shipped `.mcp.json` carries no literal secret. Its `env` block runs two shell
substitutions:

- `DATASHARE_URL` falls back to `http://localhost:8888`, the canonical local deployment,
  so the common case needs no export at all.
- `DATASHARE_API_KEY` reads the login Keychain, and falls back to the environment variable
  when there is no Keychain entry (Linux, CI, a remote instance).

Store the key once:

```bash
security add-generic-password -U -a "$USER" -s datashare-mcp -w   # paste at the prompt
security find-generic-password -s datashare-mcp -a "$USER" -w     # confirm
```

For a non-default instance, export `DATASHARE_URL` — and, off macOS, `DATASHARE_API_KEY`:

- **omp, all projects — `~/.omp/.env`:**

  ```dotenv
  DATASHARE_URL=https://datashare.example.org
  DATASHARE_API_KEY=<key>
  ```

  omp loads this into its own process environment at startup and the MCP stdio child
  inherits it. Then `chmod 600 ~/.omp/.env`.

- **omp, one project only — `<project>/.env`:** the same lines. It wins over `~/.omp/.env`.
  Full precedence, highest first: inherited process environment → `<cwd>/.env` →
  `~/.omp/agent/.env` → `~/.omp/.env` → `~/.env`. A variable already present in the process
  environment is never overwritten by any `.env` file.

- **Any harness, including Claude Code — export in your shell rc** (`~/.zshrc`), because
  only omp autoloads `.env`:

  ```bash
  export DATASHARE_URL="https://datashare.example.org"
  export DATASHARE_API_KEY="$(security find-generic-password -s datashare-mcp -a "$USER" -w 2>/dev/null)"
  ```

- **Two instances at once** — declare a second server in `~/.omp/agent/mcp.json` with an
  explicit `env` block and disable the plugin's one:

  ```json
  {
    "mcpServers": {
      "datashare-remote": {
        "type": "stdio",
        "command": "uvx",
        "args": ["--from", "datashare-mcp==0.3.0", "datashare-mcp"],
        "env": {
          "DATASHARE_URL": "https://datashare.example.org",
          "DATASHARE_API_KEY": "<key>"
        }
      }
    },
    "disabledServers": ["datashare:mcp"]
  }
  ```

  A config `env` block is an overlay on the inherited environment, not a replacement.

## Verify

```bash
omp plugin list          # expect: datashare@datashare-mcp, enabled
```

In a session, `/mcp list` shows `datashare:mcp` connected and `/mcp test datashare:mcp`
passes. Then ask the agent to list projects — it should name every project on the
instance.

**If no `mcp__datashare_mcp_*` tool appears**, check that omp's Claude-marketplace
discovery provider is not switched off:

```bash
omp config list | grep disabledProviders
```

A `claude-plugins` entry in that list disables every capability this plugin ships — MCP
server, skill and all — because plugin discovery is what loads them. Remove
`claude-plugins` from `disabledProviders` in `~/.omp/agent/config.yml`, or override it for
a single run with `--config <overlay.yml>`.

If the instance is unreachable or the key is wrong, the failure is a tool error naming the
status code, not a silent empty result.

## Pinning and updates

```bash
omp plugin marketplace update datashare-mcp
omp plugin upgrade datashare@datashare-mcp
```

That refreshes the plugin files. The **server build** is resolved and cached separately by
`uvx` from the pinned version in `.mcp.json`.

The shipped `--from` spec pins an exact release: `datashare-mcp==0.3.0`. That is
deliberate — an unpinned spec resolves to whatever is newest on every cold cache, so a
compromised release would immediately receive `DATASHARE_API_KEY` from the launch
environment. PyPI refuses to re-upload a version that already exists, so `==0.3.0` names
one immutable artifact, and `publish.yml` attaches a PEP 740 attestation naming the
workflow and commit that built it.

To move to newer server code, replace the version with the release you want and run:

```bash
uv cache clean datashare-mcp
```

Do not add `--refresh` in place of updating the version; with an exact pin it only
re-fetches the same release, and without a pin it defeats the pinning entirely.

A release bumps the version in three places at once —
`src/datashare_mcp/__init__.py`, `plugins/datashare/.claude-plugin/plugin.json`, and
`plugins[0].version` in `.claude-plugin/marketplace.json`. A stale catalog version makes
`omp plugin upgrade` silently do nothing.

## Local development

To run the server from a checkout instead of git, put this in `~/.omp/agent/mcp.json` and
disable the plugin's server:

```json
{
  "mcpServers": {
    "datashare": {
      "type": "stdio",
      "command": "uv",
      "args": ["run", "--directory", "/absolute/path/to/datashare-mcp", "datashare-mcp"]
    }
  },
  "disabledServers": ["datashare:mcp"]
}
```

This form is not plugin-namespaced, so it yields the shorter tool names
`mcp__datashare_<tool>`.
