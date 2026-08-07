"""Payload builders shaped like a real Datashare, and the shape assertions both layers share.

A hand-rolled fixture that carries only the keys the test it was written for asserts on
cannot fail when the contract changes: the test still passes because it never looked at
the key that moved. These builders emit the full shape a real instance returns, and the
assertions below are used by both the mocked suite and `tests/live/`, so the two check the
same thing rather than drifting apart.

Shapes were read off the live `tenderchad` instance (Datashare 21.2.1, Elasticsearch
8.19.8).
"""

from typing import Any

# The exact key set `get_document_content` is specified to return. `targetLanguage` is the
# one most easily dropped from a hand-written fixture, which is why the set is asserted as
# a set rather than key by key.
CONTENT_KEYS = frozenset({"content", "maxOffset", "offset", "limit", "targetLanguage"})

# What a project row carries. `name` is the only one the client depends on.
PROJECT_KEYS = frozenset({"name", "label", "sourcePath"})


def project(*, name: str = "tenderchad", **extra: Any) -> dict[str, Any]:
    """One row of the `/api/project/` list."""
    row: dict[str, Any] = {
        "name": name,
        "label": name,
        "sourcePath": "file:///home/datashare/data",
        "description": None,
        "publisherName": None,
        "maintainerName": None,
        "logoUrl": None,
        "sourceUrl": None,
    }
    row.update(extra)
    return row


def project_list(*names: str) -> list[dict[str, Any]]:
    """The `/api/project/` payload. Defaults to the two projects the local instance holds."""
    return [project(name=n) for n in (names or ("tenderchad", "local-datashare"))]


def search_hit(
    *,
    id: str = "d1",
    routing: str | None = "d1",
    source: dict[str, Any] | None = None,
    **extra: Any,
) -> dict[str, Any]:
    """One `hits.hits[]` entry, including the `_routing` a caller must carry forward."""
    hit: dict[str, Any] = {
        "_index": "tenderchad",
        "_id": id,
        "_score": 1.0,
        "_source": source
        if source is not None
        else {
            "type": "Document",
            "path": "/home/datashare/data/report.pdf",
            "contentType": "application/pdf",
            "contentLength": 4096,
            "language": "ENGLISH",
            "extractionDate": "2026-04-25T10:00:00.000Z",
            "metadata": {},
            "tags": [],
        },
    }
    if routing is not None:
        hit["_routing"] = routing
    hit.update(extra)
    return hit


def search_payload(
    *,
    hits: list[dict[str, Any]] | None = None,
    total: int | None = None,
    aggregations: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """The Elasticsearch `_search` envelope, returned by `search_documents` unmodified."""
    rows = [search_hit()] if hits is None else hits
    payload: dict[str, Any] = {
        "took": 3,
        "timed_out": False,
        "_shards": {"total": 1, "successful": 1, "skipped": 0, "failed": 0},
        "hits": {
            "total": {"value": len(rows) if total is None else total, "relation": "eq"},
            "max_score": 1.0,
            "hits": rows,
        },
    }
    if aggregations is not None:
        payload["aggregations"] = aggregations
    return payload


def type_buckets(*pairs: tuple[str, int]) -> dict[str, Any]:
    """A `terms` aggregation body, as the content-type analytics read it."""
    return {
        "content_types": {
            "doc_count_error_upper_bound": 0,
            "sum_other_doc_count": 0,
            "buckets": [{"key": k, "doc_count": n} for k, n in pairs],
        }
    }


def document_metadata(*, id: str = "d1", **extra: Any) -> dict[str, Any]:
    """The `/api/{project}/documents/{doc_id}` payload."""
    payload: dict[str, Any] = {
        "id": id,
        "path": "/home/datashare/data/report.pdf",
        "contentType": "application/pdf",
        "contentLength": 4096,
        "language": "ENGLISH",
        "extractionDate": "2026-04-25T10:00:00.000Z",
        "contentEncoding": "UTF-8",
        "status": "DONE",
        "metadata": {},
        "tags": [],
        "rootDocument": id,
        "parentDocument": None,
    }
    payload.update(extra)
    return payload


def content_payload(
    *,
    content: str = "hello",
    max_offset: int | None = None,
    offset: int = 0,
    limit: int | None = None,
    target_language: str | None = None,
) -> dict[str, Any]:
    """The content payload, carrying every key in `CONTENT_KEYS`.

    `maxOffset` defaults to the full document length rather than `len(content)`, because
    the two differ for a ranged read and conflating them is exactly the bug the shared
    assertion exists to catch.
    """
    return {
        "content": content,
        "maxOffset": len(content) if max_offset is None else max_offset,
        "offset": offset,
        "limit": len(content) if limit is None else limit,
        "targetLanguage": target_language,
    }


def assert_project_list(out: list[dict[str, Any]]) -> None:
    """Every row is a project and carries at least the keys a caller may read."""
    assert isinstance(out, list)
    for row in out:
        missing = PROJECT_KEYS - row.keys()
        assert not missing, f"project row missing {missing}: {row}"
        assert isinstance(row["name"], str) and row["name"]


def assert_search_envelope(out: dict[str, Any], *, min_hits: int = 0) -> None:
    """The raw Elasticsearch envelope, returned by the passthrough unaltered.

    Asserting the envelope rather than just `"hits" in out` is what makes a reshaping
    regression visible: if anything ever starts rewriting the response, the nested
    `hits.total.value` / `hits.hits[]._id` structure is the first thing to go.
    """
    assert "hits" in out, f"no hits envelope: {sorted(out)}"
    hits = out["hits"]
    assert "total" in hits and "value" in hits["total"]
    assert isinstance(hits["total"]["value"], int)
    rows = hits["hits"]
    assert isinstance(rows, list)
    assert len(rows) >= min_hits, f"expected >= {min_hits} hits, got {len(rows)}"
    for row in rows:
        assert "_id" in row, f"hit without _id: {sorted(row)}"
        assert "_source" in row, f"hit without _source: {sorted(row)}"


def assert_content_payload(out: dict[str, Any], *, whole_document: bool = False) -> None:
    """The five-key content contract.

    `whole_document=True` additionally asserts `len(content) == maxOffset`, the property
    that proves the probe-then-range path fetched everything rather than a prefix.
    """
    assert out.keys() >= CONTENT_KEYS, f"content payload missing {CONTENT_KEYS - out.keys()}"
    assert isinstance(out["content"], str)
    assert isinstance(out["maxOffset"], int)
    if whole_document:
        assert len(out["content"]) == out["maxOffset"], (
            f"got {len(out['content'])} chars but maxOffset is {out['maxOffset']}"
        )
