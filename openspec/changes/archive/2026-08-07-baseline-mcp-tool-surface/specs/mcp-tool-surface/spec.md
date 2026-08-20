## Purpose

Defines the observable contract of the MCP server placed in front of an ICIJ Datashare
instance: which tools and resources an MCP client can see, what shape their responses
take, and which inputs are refused before a request is ever built.

## ADDED Requirements

### Requirement: Registered tool surface

The server SHALL register exactly eight tools, named `list_projects`,
`get_project_overview`, `get_document_type_distribution`, `get_temporal_distribution`,
`get_project_summary`, `search_documents`, `get_document_metadata` and
`get_document_content`.

The four analytics tools (`get_project_overview`, `get_document_type_distribution`,
`get_temporal_distribution`, `get_project_summary`) SHALL be derived by aggregating
results of the same document-search operation that backs `search_documents`. They SHALL
NOT introduce a dependency on any additional Datashare endpoint.

#### Scenario: A client lists the available tools

- **WHEN** an MCP client requests the server's tool list
- **THEN** exactly those eight tool names are returned, and no others

#### Scenario: An analytics tool is invoked

- **WHEN** any of the four analytics tools is invoked for a project
- **THEN** the result is computed from document-search responses only, and no request is
  issued to a Datashare endpoint outside the set already used by `search_documents`,
  `get_document_metadata`, `get_document_content`, `list_projects` and the index-mapping
  resource

### Requirement: Registered resource surface

The server SHALL register exactly three resources, with the URI templates
`datashare://projects`, `datashare://index/{project}/mapping` and
`datashare://document/{project}/{doc_id}`.

#### Scenario: A client lists the available resources

- **WHEN** an MCP client requests the server's resource list
- **THEN** exactly those three URI templates are returned, and no others

### Requirement: Search is a raw Elasticsearch DSL passthrough

`search_documents(project, query)` SHALL forward `query` to Elasticsearch unmodified.
The server SHALL NOT inject, rewrite, wrap, default or validate any part of the query
body, and SHALL return the Elasticsearch response envelope as received.

This is a contract, not an implementation accident: Datashare's `IndexResource`
documents `@Post("/search/:path:")` as a proxy where "everything sent is forwarded to
Elasticsearch". Callers depend on being able to express any query the index supports,
and on reading `hits.hits[]._routing` out of the raw envelope in order to address
documents through the other tools.

#### Scenario: A query body is submitted

- **WHEN** `search_documents` is called with a query body
- **THEN** the body reaching Elasticsearch is byte-for-byte the body supplied by the
  caller

#### Scenario: A query uses a feature the server does not know about

- **WHEN** the query body uses an Elasticsearch feature the server has no awareness of
- **THEN** the query is still forwarded, and the response is returned unaltered rather
  than rejected

### Requirement: Document content response shape

`get_document_content` SHALL return an object carrying the keys `content`, `maxOffset`,
`offset`, `limit` and `targetLanguage`.

When called with neither `offset` nor `limit`, it SHALL return the entire document, such
that `len(content) == maxOffset`.

The whole-document case SHALL NOT be served by Datashare's unranged content route. That
route reads the document text from the relational `document` table, which is empty for a
corpus indexed by the Datashare CLI (`SCAN`/`INDEX` populate Elasticsearch, not the
relational store), and answers HTTP 500. The whole-document case SHALL instead be
satisfied by the Elasticsearch-backed ranged route.

#### Scenario: Content is requested with no range

- **WHEN** `get_document_content` is called without `offset` and without `limit`
- **THEN** the returned object carries all five keys, `content` holds the entire
  document, and `len(content)` equals `maxOffset`

#### Scenario: The document is stored only in the index

- **WHEN** the requested document was indexed by the Datashare CLI and has no row in the
  relational `document` table
- **THEN** the whole-document request still succeeds rather than failing with HTTP 500

#### Scenario: A byte range is requested

- **WHEN** `get_document_content` is called with both `offset` and `limit`
- **THEN** only that range is returned, and `maxOffset` still reports the full document
  length

### Requirement: Offset and limit are refused unless paired

`get_document_content` SHALL raise `ValueError` when exactly one of `offset` and `limit`
is supplied. Supplying both, or neither, SHALL be accepted.

#### Scenario: Only offset is supplied

- **WHEN** `get_document_content` is called with `offset` but no `limit`
- **THEN** `ValueError` is raised and no request is sent

#### Scenario: Only limit is supplied

- **WHEN** `get_document_content` is called with `limit` but no `offset`
- **THEN** `ValueError` is raised and no request is sent

### Requirement: Path segments are validated before a request is built

Every `project` and `doc_id` value SHALL be matched against `[A-Za-z0-9._-]+` in full.
A value that does not match SHALL raise `ValueError` before any request is constructed,
so that no unvalidated caller input can reach the URL path.

The character set excludes `/`, `%`, whitespace and every other character, which is what
keeps a caller-supplied value from traversing into or forging a different Datashare
route.

#### Scenario: A project name contains a path separator

- **WHEN** a tool is called with a `project` of `../admin`
- **THEN** `ValueError` is raised and no request is sent

#### Scenario: A document id is percent-encoded

- **WHEN** a tool is called with a `doc_id` containing `%2F`
- **THEN** `ValueError` is raised and no request is sent

#### Scenario: An ordinary identifier is supplied

- **WHEN** a tool is called with `project` of `demo` and a document id of
  alphanumerics, dots, underscores or hyphens
- **THEN** the value is accepted and the request proceeds
