# Enforce read-only with an outgoing-request allowlist

## Why

The README calls this server "read-only by design", but nothing enforces it. Today the
guarantee rests on the fact that `client.py` happens to call only five endpoints, and on
Datashare's own server-side checks. Both are weak:

- **Nothing stops a future call site.** A new tool, a helper, or a redirect can issue any
  request the bearer key permits. There is no place a reviewer can look to see the
  surface, and no test that fails when it widens.
- **Datashare's own check is narrower than it looks, and is not ours.**
  `IndexAccessVerifier.isAuthorizedRequest` gates the Elasticsearch proxy with
  `areAllIndexesGranted && (isMethodGet || isSearchPath || isCountPath)`. `isMethodGet` is
  an unconditional OR branch, so for **GET any Elasticsearch path is forwarded**. That is
  a property of this Datashare version and mode, not a promise to this client.
- **`/api/index` carries destructive neighbours.** `IndexResource` is
  `@Prefix("/api/index")` and registers `PUT /api/index/:index` — which has **no mode
  check at all** — plus `POST /api/index/:index/_close`, `_open`, and the whole
  `_snapshot` family, all enabled in exactly the `LOCAL` mode this deployment runs. They
  share a prefix with the two endpoints this server legitimately calls, and the only
  thing separating them is the path.

The mode this instance runs in makes it worse rather than better: LOCAL mode does not
enforce API-key authentication (only `LocalUserFilter`), so "the key is read-only" is not
an available defence.

## What Changes

- **New** `src/datashare_mcp/readonly.py`: a fixed allowlist of five `(method, path)`
  pairs, a `ReadOnlyViolation` error, an `is_read_only` predicate, and a
  `read_only_hook` factory.
- The client registers the hook as an httpx **request** event hook, so it runs before any
  request leaves the process, redirect hops included.
- Requests that leave the configured host are refused, so a redirect cannot re-send a
  request body somewhere else.
- `httpx` is pinned below `1` because the guard depends on request-event-hook semantics.
- New `tests/test_readonly.py` covering the allowed set, the refused set, the end-to-end
  "no request reached the wire" case, redirects, and the decoded-path tripwire.

No **BREAKING** changes: the five allowed pairs are exactly the endpoints `client.py`
already calls, so every existing tool and resource behaves identically.

## Capabilities

### New Capabilities

- `read-only-enforcement`: what the server refuses to send, and where that decision is
  made.

### Modified Capabilities

None. `mcp-tool-surface` describes what the tools do; nothing about their behaviour
changes.

## Impact

- Adds `src/datashare_mcp/readonly.py` and `tests/test_readonly.py`.
- One line in `DatashareClient.__init__` wires the hook.
- `pyproject.toml`: `httpx>=0.27` becomes `httpx>=0.27,<1`.
- Any future change that needs a sixth Datashare endpoint must widen the allowlist
  explicitly, in its own change. That is the point.
