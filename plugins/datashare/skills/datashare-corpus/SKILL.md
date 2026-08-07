---
name: datashare-corpus
description: Use when querying or exploring a corpus held in an ICIJ Datashare instance — size and shape the project first, read the Elasticsearch mapping before writing a query, and page document text correctly instead of guessing at field names.
---

# Datashare corpus

## Objective

Turn a Datashare project from an opaque pile of documents into something you can query
deliberately: know how big it is and what is in it before you search, write queries against
the mapping rather than against guessed field names, and read only the text you need.

## When to use

- A `datashare://` MCP server is connected and you need to find or read something in one of
  its projects.
- You are about to call `search_documents` — read this first, because that tool gives you
  no schema help whatsoever.

## The six things agents get wrong

### 1. Read the mapping before writing a query

`search_documents(project, query)` is a **raw Elasticsearch DSL passthrough**. The body you
supply is forwarded to Elasticsearch unmodified: nothing validates it, nothing corrects a
field name, and nothing tells you what fields exist. A query against a field that is not in
the mapping returns zero hits, not an error — indistinguishable from a corpus that genuinely
has no match.

So read `datashare://index/{project}/mapping` first, and write the query against what is
actually there.

### 2. Size and shape the corpus before searching it

Three tools answer "what am I even looking at", cheaply:

- `get_project_overview(project)` — document count, language distribution, creation-date range.
- `get_document_type_distribution(project)` — content types with counts and percentages.
- `get_temporal_distribution(project)` — documents by year, with peaks called out.

`get_project_summary(project, format)` runs all three and returns them together; pass
`format="markdown"` when the answer is going straight to a human.

Use them before searching. A search returning 3 hits means something very different in a
corpus of 40 documents than in one of 40,000, and a date filter is worthless until you know
which years the corpus actually covers. These are aggregations over the same `_search`
endpoint, not extra round trips to some other service.

### 3. Keep `size` small and iterate

The passthrough returns whatever Elasticsearch returns. Ask for `size: 10`, look at what
comes back, refine, ask again. A large `size` on a broad query buries the signal and wastes
the context window on documents you will not read.

Use `_source` filtering in the query body to pull back only the fields you need.

### 4. Carry `_routing` from the search hit

Datashare shards by document, and child documents (an email attachment, a page of a
container file) live on the shard of their parent. Each hit in the `_search` response
carries `_routing`. Pass it through:

```
get_document_metadata(project, doc_id, routing=<hit._routing>)
get_document_content(project, doc_id, routing=<hit._routing>)
```

Dropping it makes a document that plainly exists in your search results come back as "not
found (404)".

### 5. Page long documents with `offset` **and** `limit`

`get_document_content` with neither `offset` nor `limit` returns the **whole** document, and
`len(content) == maxOffset`. That is fine for a memo and wrong for a 900-page PDF.

To page, supply **both** `offset` and `limit`. Supplying exactly one raises an error — this
is deliberate, not a quirk to work around. The first response's `maxOffset` tells you the
full length, so you know how many pages remain.

### 6. The server is read-only and will refuse the rest

Five Datashare endpoints back everything above. Every outgoing request is matched against a
fixed allowlist and refused before it is sent, whatever the API key or the instance's mode
would permit.

Do not attempt ingestion, indexing, tagging, index creation, snapshotting, or any
`_delete_by_query`-style Elasticsearch write through this server. Those are refused, and the
refusal is the design rather than a misconfiguration to route around. If a task genuinely
needs to write to Datashare, say so and stop — do not look for another path.

## Method

1. `list_projects()` — or read `datashare://projects` — to learn what exists.
2. `get_project_summary(project)` to size and shape it.
3. Read `datashare://index/{project}/mapping`.
4. `search_documents(project, query)` with a small `size`, written against the mapping.
5. For each hit worth reading: `get_document_metadata` with its `_routing`, then
   `get_document_content` — whole if short, `offset` + `limit` if not.
6. Cite by document id and project, so the claim can be checked.
