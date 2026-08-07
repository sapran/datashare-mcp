## Purpose

Defines the read-only guarantee as an enforced property of this server rather than an
emergent one: which outgoing requests are refused, at what point they are refused, and
what a caller observes when refusal happens.

## ADDED Requirements

### Requirement: Every outgoing request is checked before it is sent

The server SHALL evaluate every request it is about to issue, including each hop of a
redirect chain, against a fixed allowlist of `(method, path)` pairs. A request that does
not match SHALL be refused by raising `ReadOnlyViolation`, and SHALL NOT reach the
network.

The check SHALL be independent of what the Datashare instance would permit. It SHALL hold
regardless of the privileges of the configured API key and regardless of the mode the
instance runs in — in particular under `LOCAL` mode, where Datashare does not enforce
API-key authentication at all (only `LocalUserFilter`).

#### Scenario: A mutating request is issued directly on the transport

- **WHEN** code inside this server issues `POST /api/index/tenderchad/_close` on the
  client's transport
- **THEN** `ReadOnlyViolation` is raised, and the Datashare instance receives no request

#### Scenario: A redirect points at a mutating endpoint

- **WHEN** an allowed request is answered with a redirect whose target is outside the
  allowlist
- **THEN** the redirect target receives no request

### Requirement: Redirects are not followed

The server SHALL NOT follow HTTP redirects. A 3xx response SHALL surface to the caller
as an error rather than being replayed against the redirect target.

This is the first of two independent defences. It means no request can be replayed at
all, whatever the target. The allowlist check is the second: because it runs on every
request the transport sends, the guarantee survives redirect-following ever being
enabled. Both SHALL hold; neither SHALL be removed on the grounds that the other exists.

#### Scenario: An allowed read is answered with a redirect

- **WHEN** `GET /api/project/` is answered with a 307 to any location
- **THEN** the caller receives an error naming the 307, and the redirect target receives
  no request and no credential

#### Scenario: Redirect-following is enabled in future

- **WHEN** the transport is configured to follow redirects and a hop targets a path
  outside the allowlist
- **THEN** `ReadOnlyViolation` is raised on that hop, and the hop's target receives no
  request

### Requirement: Index administration routes are refused

The server SHALL refuse every route Datashare's `IndexResource` registers other than the
two it legitimately calls. Refusal SHALL cover at least: creating an index
(`PUT /api/index/:index`), closing and opening an index
(`POST /api/index/:index/_close`, `POST /api/index/:index/_open`), and every snapshot
route (`PUT`, `GET` and `DELETE /api/index/_snapshot/:repository`,
`DELETE /api/index/_snapshot/:repository/:snapshot`, and
`POST /api/index/_snapshot/:repository/:snapshot/_restore`).

These share the `/api/index` prefix with the search and mapping endpoints this server
does call. `PUT /api/index/:index` carries **no mode check whatsoever**, and the rest are
gated only by `modeVerifier.checkAllowedMode(Mode.LOCAL, Mode.EMBEDDED)` — which admits
the LOCAL mode this deployment runs. Nothing on the Datashare side refuses them, so the
path pin is the whole of the protection.

#### Scenario: An index-creation request is attempted

- **WHEN** `PUT /api/index/tenderchad` is attempted
- **THEN** it is refused

#### Scenario: An index-close request is attempted

- **WHEN** `POST /api/index/tenderchad/_close` is attempted
- **THEN** it is refused, and the corpus is not taken offline

#### Scenario: A snapshot restore is attempted

- **WHEN** `POST /api/index/_snapshot/backup/snap1/_restore` is attempted
- **THEN** it is refused

### Requirement: Elasticsearch write operations are refused by path

The server SHALL refuse every Elasticsearch operation reachable through Datashare's
search proxy other than `_search`. Refusal SHALL cover at least `_delete_by_query`,
`_update_by_query` and `_bulk` under `POST`, and SHALL restrict `GET` to the literal
`_mapping` suffix.

Datashare's `IndexAccessVerifier.isAuthorizedRequest` narrows the proxy with
`areAllIndexesGranted && (isMethodGet || isSearchPath || isCountPath)`. Two consequences
make this requirement load-bearing rather than redundant:

- `isMethodGet` is an unconditional OR branch, so under `GET` **any** Elasticsearch path
  is forwarded. Pinning `GET` to `_mapping` is the only thing that stops a GET reaching
  arbitrary cluster endpoints.
- The `POST` narrowing is a property of this Datashare version and mode. Relying on it
  would make the guarantee Datashare's rather than this server's.

#### Scenario: A delete-by-query is attempted

- **WHEN** `POST /api/index/search/tenderchad/_delete_by_query` is attempted
- **THEN** it is refused before the request is sent, whether or not Datashare would also
  have refused it

#### Scenario: A GET reaches for an arbitrary cluster path

- **WHEN** `GET /api/index/search/tenderchad/_cluster/settings` is attempted
- **THEN** it is refused, even though Datashare's own check would forward any GET path

### Requirement: A request leaving the configured host is refused

The server SHALL refuse any request whose host differs from the host of the configured
Datashare URL.

Without this, a redirect could re-send a request — a 307 preserves both method and body,
and the client attaches the bearer API key to every request — to a host the operator
never configured. Redirects are not followed today, so this requirement is what keeps
that from silently becoming exploitable if they ever are.

#### Scenario: A request targets another host

- **WHEN** a request is issued whose host differs from the configured one
- **THEN** `ReadOnlyViolation` is raised naming the configured host, and the other host
  receives no request and no credential

#### Scenario: A redirect points at another host

- **WHEN** an allowed request is answered with a redirect to a different host
- **THEN** the other host receives no request and no credential

### Requirement: A base path prefix does not weaken matching

When the configured Datashare URL carries a path prefix, the server SHALL strip that
prefix before matching, so an instance mounted under a sub-path is checked on
`/api/...` exactly like a root-mounted one. A request whose path does not begin with the
configured prefix SHALL be refused.

#### Scenario: An instance is mounted under a sub-path

- **WHEN** the configured URL is `https://ds.test/datashare` and the server issues
  `GET https://ds.test/datashare/api/project/`
- **THEN** the request is allowed, having matched `/api/project/`

#### Scenario: A request escapes the configured prefix

- **WHEN** the configured URL is `https://ds.test/datashare` and a request targets
  `https://ds.test/api/project/`
- **THEN** it is refused

### Requirement: Widening the surface requires widening the allowlist

The allowlist SHALL be the single place that determines which requests may be sent. No
tool argument, caller-supplied value, configuration setting or redirect SHALL be able to
admit a request the allowlist does not already contain.

The path-segment pattern used by the allowlist SHALL match the character set the client
validates caller input against, so the validator and the guard cannot disagree. That set
SHALL exclude `%`, which is the tripwire for matching a decoded path: if `%` were ever
admitted, matching would have to move to the raw path.

#### Scenario: A caller supplies a value intended to reach another route

- **WHEN** a caller supplies a `project` or `doc_id` value crafted to extend the URL path
- **THEN** the request is refused, either by input validation or by the allowlist, and is
  not sent

#### Scenario: The accepted character set is inspected

- **WHEN** the path-segment pattern used by the allowlist is inspected
- **THEN** it does not admit `%`
