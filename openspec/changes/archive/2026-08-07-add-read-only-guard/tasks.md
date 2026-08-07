## 1. The guard module

- [x] 1.1 Add `src/datashare_mcp/readonly.py` with `_SEGMENT`, `_ALLOWED`,
      `ReadOnlyViolation`, `is_read_only` and `read_only_hook`, modelled structurally on
      `aleph-mcp/src/aleph_mcp/readonly.py`.
- [x] 1.2 Write the module comment stating the verified threat, citing
      `IndexResource.java` and `IndexAccessVerifier.java` at tag `21.2.1` by path and by
      the specific route or expression each claim rests on.

## 2. Wiring

- [x] 2.1 Pass `event_hooks={"request": [read_only_hook(settings.url)]}` to the
      `httpx.AsyncClient(...)` constructor in `DatashareClient.__init__`. No other call
      site changes.
- [x] 2.2 Change `httpx>=0.27` to `httpx>=0.27,<1` in `pyproject.toml`, with a comment
      naming the guard as the reason.

## 3. Mocked tests

- [x] 3.1 Parametrised allow-cases: the five pairs with a concrete project and document
      id, plus a trailing-slash case.
- [x] 3.2 Parametrised refuse-cases for the `IndexResource` administration routes, the
      Elasticsearch write operations, the arbitrary-GET case, `DELETE` on the document
      route and `POST /api/project/`.
- [x] 3.3 End-to-end case: a direct mutating call on `client._http` raises
      `ReadOnlyViolation` and the respx route records zero calls.
- [x] 3.4 Redirect cases: a hop into a refused path, and a cross-host hop.
- [x] 3.5 Base-path cases: prefix stripped before matching, and a request escaping the
      prefix refused.
- [x] 3.6 `test_segment_charset_excludes_percent` as the decoded-path tripwire.
- [x] 3.7 Confirm the whole mocked suite still passes — every existing tool exercises the
      client with the hook now installed, so a missing allowlist pair fails here.

## 4. Live verification

Separate from the mocked work: needs a running Datashare instance, which a contributor
may not have.

- [x] 4.1 Run the live suite against the local instance and confirm all four smoke tests
      pass, proving no legitimate read was blocked by the allowlist.
      *4 passed against `tenderchad` (4884 documents) on `http://localhost:8888`.*
