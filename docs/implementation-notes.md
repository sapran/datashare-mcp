# Implementation notes

Findings recorded during other work, kept out of the change that surfaced them. An entry
leaves this file when it becomes a spec requirement or is fixed — not when someone
remembers to tidy up.

## The version is recorded in three places

`0.3.0` now appears in `src/datashare_mcp/__init__.py` (the package version, which
`pyproject.toml` reads dynamically), in `plugins/datashare/.claude-plugin/plugin.json`,
and as `plugins[0].version` in `.claude-plugin/marketplace.json`. A release must bump all
three together.

The failure mode is silent rather than loud: `omp plugin upgrade` compares the installed
version against the **catalog** version, so a stale `marketplace.json` makes the upgrade a
no-op and the user keeps running old plugin files with no error to notice. Nothing checks
the three for agreement today.

## `plugins/datashare/.mcp.json` is executable, not configuration

The MCP host executes the `!`-prefixed `env` values on every server start, so any change
to that file is arbitrary command execution on every user who installs or upgrades the
plugin. It must never be reviewed as a config-only diff.

The two commands are injection-free as written — both are fixed command strings, every
expansion is double-quoted, and `printf %s` takes the value as an argument rather than as
a format string — and the only inputs are the operator's own `$USER`, `$DATASHARE_URL`,
`$DATASHARE_API_KEY` and login Keychain. Recorded so the property is a checked one rather
than an assumed one.

## The `uvx --from` spec is pinned to a commit SHA

`plugins/datashare/.mcp.json` installs from
`git+https://github.com/sapran/datashare-mcp.git@<40-char sha>`. An unpinned spec would
resolve the moving default-branch HEAD on every cold cache and hand the resulting code
`DATASHARE_API_KEY`, so push access to the repository would reach every installed host.
Bumping the server means replacing the SHA everywhere it appears literally. Six
occurrences, three files: `plugins/datashare/.mcp.json`, the "Two instances at once"
example at `plugins/datashare/README.md:136`, and the install command plus three client
examples in `README.md`. The `Pinning and updates` prose writes `<sha>` as a placeholder
and needs no edit. `git grep -c '<old sha>'` should report 6 before tagging; nothing checks
them for agreement. A tag is mutable and does not replace the SHA.

**This deliberately is not PyPI.** Publishing was tried and reverted. Be precise about what
that does and does not buy, because the obvious claim is wrong:

A `git+` install is an sdist build, not a wheel download. uv resolves the build backend
(`hatchling>=1.25,<2`) from PyPI into an isolated PEP 517 environment that `uv.lock` does
not cover, executes it, and then resolves `fastmcp`, `httpx` and `pydantic-settings` from
PyPI as well — `uv.lock` does not apply to a `uvx` or `uv tool` install either. So PyPI is
still in the trust path on every cold cache, and this route strictly *adds* build-time code
execution that a published wheel would not have, and gives up the PEP 740 attestation.

What the SHA pin actually buys is narrower and still worth having: the *first-party* code —
the part that receives `DATASHARE_API_KEY` — is fixed to one reviewed revision, so push
access to this repository does not reach installed hosts. Third-party dependency risk is
unchanged either way and is bounded only by the version ranges in `pyproject.toml`.

Two known costs, accepted rather than solved: a SHA cannot be written before the commit it
names exists, so the pin trails the version bump by one commit; and `git` becomes a
run-time prerequisite, because uv shells out to the `git` binary for `git+` sources.

## `get_document_content` and `list_projects` return unvalidated JSON

`client.py` narrows `resp.json()` with `cast(...)`, which is a compile-time assertion and
performs no runtime check. Two of those payloads are destructured by callers:
`server.py`'s `document_resource` calls `.get("content", "")` on the content payload, and
`projects_resource` returns the project list straight through. A Datashare that answered
`200` with a JSON scalar or list where a dict is expected would raise `AttributeError`
rather than a `ToolError` naming the endpoint.

Not observed against a real instance, and `raise_for_status` already covers every non-2xx
case. The fix, if it is ever worth making, is an `isinstance` guard at those two decode
sites, raising the same `ToolError` the rest of the client raises.
