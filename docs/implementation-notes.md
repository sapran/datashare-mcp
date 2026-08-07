# Implementation notes

Findings recorded during other work, kept out of the change that surfaced them. An entry
leaves this file when it becomes a spec requirement or is fixed — not when someone
remembers to tidy up.

## The version is recorded in three places

`0.2.0` now appears in `src/datashare_mcp/__init__.py` (the package version, which
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
Bumping the server means replacing the SHA with a reviewed commit, in `.mcp.json`, the
manual-install example and the `Pinning and updates` section of
`plugins/datashare/README.md`. A tag is mutable and does not replace the SHA.

## `get_document_content` and `list_projects` return unvalidated JSON

`client.py` narrows `resp.json()` with `cast(...)`, which is a compile-time assertion and
performs no runtime check. Two of those payloads are destructured by callers:
`server.py`'s `document_resource` calls `.get("content", "")` on the content payload, and
`projects_resource` returns the project list straight through. A Datashare that answered
`200` with a JSON scalar or list where a dict is expected would raise `AttributeError`
rather than a `ToolError` naming the endpoint.

Not observed against a real instance, and `raise_for_status` already covers every non-2xx
case. The fix, if it is ever worth making, is an `isinstance` guard at those two decode
sites — `aleph-mcp/src/aleph_mcp/client.py` does exactly that.
