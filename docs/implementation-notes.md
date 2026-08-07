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
