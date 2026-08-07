# Design — read-only enforcement

## Context

See proposal.md — Why. The relevant current state: `DatashareClient` owns a single
`httpx.AsyncClient` built in `__init__`, and every request in the module goes through it.
Ten call sites reach five distinct endpoints. Sibling project `aleph-mcp` already solves
the same problem, and its `src/aleph_mcp/readonly.py` is the structural model here.

## Goals / Non-Goals

**Goals**

- Make the read-only property enforced at one chokepoint, reviewable in one file.
- Hold the property on redirect hops, not just on requests this code writes.
- Make the guard's charset and the client's input validator provably agree.

**Non-Goals**

- Closing the *query-string* surface. `aleph-mcp` needs that because it builds query
  parameter names from caller values. This client does not: every parameter name here is
  a literal it chose (`routing`, `offset`, `limit`, `targetLanguage`), and the one
  caller-controlled blob — the Elasticsearch DSL query — travels in a JSON body to an
  endpoint whose contract is "forward this unmodified".
- Restricting what the Elasticsearch DSL body may contain. `search_documents` is
  specified as a passthrough; narrowing it would break the `mcp-tool-surface` contract.
  The `POST` method is pinned to the `_search` path, which is where the boundary lives.
- Rate limiting, auditing, or anything about *reads*.

## Decisions

**An httpx request event hook, not a wrapper method.** A wrapper only guards calls that
remember to use it, and it never sees a redirect. Registering
`event_hooks={"request": [...]}` on the `AsyncClient` means the check runs for every
request the transport is about to make, including each hop httpx follows. Alternative
considered: a custom `httpx.AsyncBaseTransport`. Rejected — same coverage, more surface,
and it entangles the guard with connection handling.

**Refuse by raising, not by returning a synthetic response.** A synthetic 403 would be
indistinguishable from Datashare refusing, and could be swallowed by
`raise_for_status`. `ReadOnlyViolation` derives from `RuntimeError` and propagates,
matching `aleph-mcp`.

**Match on `(method, path)` with `fullmatch`, and never drop the method.** Two of the
five endpoints share a prefix with destructive Datashare routes, and one —
`/api/{project}/documents/{doc_id}` — is a live DELETE route upstream on the identical
path. Only the absence of a `(DELETE, …)` pair refuses it.

**Reuse the client's charset for path segments.** `_SEGMENT` is
`[A-Za-z0-9._-]+`, the same set as `_SAFE_PATH_SEGMENT` in `client.py`. Two reasons:
the validator and the guard cannot drift apart, and excluding `/` is what stops
`/api/{project}/documents/{doc_id}` from also matching
`/api/{project}/documents/content/{doc_id}` — two distinct rules that would otherwise
collapse into one.

**Match the decoded path, with a tripwire.** `request.url.path` is percent-decoded, so
`%2F` becomes `/` before matching — decoding can only *add* separators, which makes
`fullmatch` stricter, never looser. That reasoning holds only while `%` is outside the
accepted charset, so a test asserts `"%" not in _SEGMENT`. If that ever changes, the
guard must move to `raw_path`.

**Pin `httpx<1`.** The guard's correctness is a statement about hook semantics — that
the request hook fires per hop and that raising aborts the send. A major-version bump
could change either silently. Alternative considered: leaving it open and relying on the
tests. Rejected: the tests would catch it, but only after a routine upgrade had already
shipped a weaker guarantee.

**Leave `follow_redirects` at `False`.** Discovered while writing the tests: unlike
`aleph-mcp`, which needs `follow_redirects=True`, this client never set it, so httpx's
default applies and a 3xx surfaces as an error instead of being replayed. That is the
stronger position and no Datashare endpoint this server calls redirects, so it stays.
The consequence is that the hook's host pin and its per-hop checking are *latent* today
rather than active — they are what keeps the guarantee intact if someone later enables
redirect-following. Alternative considered: turning redirect-following on so the hook's
redirect handling is exercised end to end. Rejected — that would weaken real behaviour
to make a test more direct. The hook is unit-tested against constructed requests
instead, and a test asserts `follow_redirects is False` so the pairing cannot drift.

## Risks / Trade-offs

- **A future endpoint is added and the author widens the allowlist reflexively.** →
  `openspec/config.yaml` carries an apply-time rule that widening the allowlist is never
  an incidental step; it gets its own change. The allowlist file also carries the
  rationale inline so a reader meets it before editing.
- **The allowlist drifts from what `client.py` actually calls, silently blocking a real
  read.** → The mocked suite exercises every tool through the real client with the hook
  installed, so a missing pair fails loudly rather than in production.
- **Percent-decoding assumptions.** → Covered by the `%`-charset tripwire above.
- **False sense of completeness.** The guard restricts *what is sent*, not what is
  returned or what a caller does with it. It is not an authorization system.

## Migration Plan

None required. The five allowed pairs are exactly the endpoints already in use, so the
change is behaviour-preserving for every existing tool and resource. Rollback is removing
the `event_hooks` argument.

## Open Questions

None.
