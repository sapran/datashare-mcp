# Baseline the MCP tool surface

## Why

This is a **baseline of already-shipped behaviour, not new work.** Every tool, resource
and refusal described here exists on `develop` today: the four original tools shipped
before this repository adopted OpenSpec, and the four analytics tools shipped in
`5d3e232 feat: add project analytics tools`.

The surface has no written contract. That matters more than usual here, because two of
its properties are easy to "simplify" away by someone reading only the code:
`search_documents` forwards its body to Elasticsearch unmodified, and
`get_document_content` deliberately refuses to call Datashare's own unranged content
path. Both look like accidents. Writing them down as requirements is what stops the next
change from undoing them.

## What Changes

Nothing in the code. This change records the contract of what is already there:

- The eight registered tools and their names.
- The three registered resources and their URI templates.
- `search_documents` as a raw Elasticsearch DSL passthrough.
- The `get_document_content` response key set, its whole-document behaviour, and the
  `ValueError` raised when exactly one of `offset`/`limit` is supplied.
- Path-segment validation of `project` and `doc_id` before any request is built.

No **BREAKING** changes; no behaviour is added, altered or removed.

## Capabilities

### New Capabilities

- `mcp-tool-surface`: the observable contract of the MCP server — which tools and
  resources exist, what they return, and what they refuse.

### Modified Capabilities

None. `openspec/specs/` is empty before this change.

## Impact

- Documentation only: adds `openspec/specs/mcp-tool-surface/spec.md`.
- Describes, without modifying, `src/datashare_mcp/server.py` (registration) and
  `src/datashare_mcp/client.py` (transport and validation).
- No dependency, API or deployment impact.
