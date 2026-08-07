## 1. Verify the baseline against the shipped code

This change adds no behaviour. Every task below is a verification that the spec matches
what `develop` already does — if one fails, the spec is wrong and must be corrected, not
the code.

- [x] 1.1 Confirm `src/datashare_mcp/server.py` registers exactly the eight `@mcp.tool`
      functions named in the spec, and no others.
- [x] 1.2 Confirm `src/datashare_mcp/server.py` registers exactly the three
      `@mcp.resource` URI templates named in the spec, and no others.
      *Corrected the spec: the third template is `datashare://document/{project}/{doc_id}`,
      not `{id}`.*
- [x] 1.3 Confirm every `self._http.get`/`self._http.post` call in
      `src/datashare_mcp/client.py` targets one of five distinct endpoints, and that the
      four analytics methods all reach `/api/index/search/{project}/_search`.
      *Ten call sites, five distinct endpoints: `GET /api/project/`,
      `POST /api/index/search/{project}/_search`,
      `GET /api/index/search/{project}/_mapping`,
      `GET /api/{project}/documents/{doc_id}`,
      `GET /api/{project}/documents/content/{doc_id}`.*
- [x] 1.4 Confirm `DatashareClient.search` passes the caller's `query` straight through as
      the JSON body with no mutation.
- [x] 1.5 Confirm `get_document_content` with neither `offset` nor `limit` probes with
      `limit=0` and then fetches `0..maxOffset` via the ranged route, never the unranged
      one.
- [x] 1.6 Confirm the half-supplied `offset`/`limit` case raises `ValueError`.
- [x] 1.7 Confirm `_SAFE_PATH_SEGMENT` is anchored `^[A-Za-z0-9._-]+$` and is applied to
      every `project` and `doc_id` before a URL is built.
      *Every public method that takes a path segment validates it; the three private
      helpers that build URLs are reachable only through a validating public method.*

## 2. Confirm the mocked suite already covers the baseline

- [x] 2.1 Run `uv run pytest -q` and confirm it passes with no new tests added.
      *51 passed, 4 skipped (live suite).*
- [x] 2.2 Confirm the existing mocked tests already assert the passthrough body, the
      five content keys, the paired-argument `ValueError` and the path-segment rejection;
      record any requirement with no covering test as an entry in
      `docs/implementation-notes.md` rather than widening this change.
      *Covered: `test_search_passes_through_body` asserts the body reaching the wire;
      `pytest.raises(ValueError, match="together")` covers the paired-argument rule;
      `match="invalid project"` and `match="invalid doc_id"` cover path-segment
      rejection; the probe-then-range path is asserted directly. One gap: the content
      fixtures omit `targetLanguage`, so the five-key set is not asserted as a set.
      Closed by the shared shape builders rather than by a note.*
