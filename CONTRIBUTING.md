# Contributing

Thanks for looking. This is a small, deliberately narrow project: a read-only MCP server
over the ICIJ Datashare API. Changes that widen what it can send are held to a high bar —
see below.

## Getting set up

Requires Python ≥ 3.12 and [`uv`](https://github.com/astral-sh/uv).

```bash
git clone https://github.com/sapran/datashare-mcp.git
cd datashare-mcp
uv sync --all-extras
```

A local Datashare to test against is a Docker Compose project: copy `env.example` to
`.env`, point `DATASHARE_DATA_DIR` at a directory of documents, then `docker compose up -d`.

## What CI runs

These four, on Python 3.12 and 3.13. Run them before opening a pull request:

```bash
uv run ruff check .
uv run mypy
uv run pytest -q
uv build
```

`mypy` is configured `strict`. New code is expected to type-check without `# type: ignore`.

The mocked suite uses `respx` and hits no network. Four tests under `tests/live/` need a
real instance and are skipped unless `DATASHARE_LIVE_TESTS=1`:

```bash
DATASHARE_URL=... DATASHARE_API_KEY=... DATASHARE_LIVE_TESTS=1 \
  DATASHARE_LIVE_PROJECT=<your-project> uv run pytest tests/live
```

## Pull requests

- Branch from `develop` and target `develop`. `main` tracks releases.
- One logical change per commit, with a [Conventional
  Commits](https://www.conventionalcommits.org) prefix: `feat:`, `fix:`, `chore:`,
  `docs:`, `refactor:`, `test:`.
- Explain *why* in the commit message or PR description. The code says what.
- New behaviour needs a test. A test that cannot fail proves nothing — if you are fixing a
  bug, make the test red against the old code first.

## Two files that are not what they look like

**`src/datashare_mcp/readonly.py`** holds the allowlist of five `(method, path)` pairs that
is the entire security boundary of this project. Adding a pair widens what the server can
send to a Datashare instance that, in LOCAL mode, authenticates nobody. Any PR touching it
needs to say which tool needs the endpoint and why the existing five do not suffice.

**`plugins/datashare/.mcp.json`** is executed, not merely parsed. The MCP host runs its
`!`-prefixed `env` values as shell commands on every server start, so a change there is
arbitrary command execution on every machine that installs or upgrades the plugin. It is
never a config-only diff and must never be reviewed as one.

## Reporting security issues

Not here — see [SECURITY.md](SECURITY.md). Do not open a public issue for something
exploitable.
