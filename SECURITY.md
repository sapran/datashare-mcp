# Security policy

## Reporting a vulnerability

Report privately through GitHub's [private vulnerability
reporting](https://github.com/sapran/datashare-mcp/security/advisories/new). Please do not
open a public issue for something exploitable.

Expect an acknowledgement within seven days. This is a community project maintained in
spare time — there is no paid response commitment, and no bounty.

Include what you need to make the finding reproducible: the version, the request or tool
call that triggers it, and what you expected instead. Never include a real API key or any
document from a real corpus.

## Supported versions

The most recent release only. Fixes are published as a new version on PyPI; there are no
backports.

## What counts as a vulnerability here

This server's security claim is narrow and specific: **it cannot be made to send a request
outside a fixed allowlist of five read endpoints**, and **corpus content cannot escape the
frame that marks it as untrusted data**. Findings that break either are in scope:

- Any way to make the server issue an outgoing HTTP request whose `(method, path)` is not
  one of the five pairs in `src/datashare_mcp/readonly.py` — including through a tool
  argument, a redirect hop, a URL-encoding trick, or a path-normalisation difference
  between the guard and the server.
- Any way to reach a different origin than the configured `DATASHARE_URL`.
- Any way for document text or metadata to break out of the nonce-bound
  `BEGIN/END UNTRUSTED DOCUMENT` framing, or to forge the `_notice` marker, so that corpus
  content could be read as instructions rather than as evidence.
- Any way past the search-body guard (`_scan_body` in `src/datashare_mcp/client.py`) to
  get server-side scripting, `runtime_mappings`, or a cross-index document reference
  forwarded to Elasticsearch.
- Leakage of `DATASHARE_API_KEY` into a response, an error message, or a log line.

## What is out of scope

**That a `--mode=LOCAL` Datashare does not authenticate anyone is upstream deployment
behaviour, not a defect in this server.** In that mode Datashare binds `CsrfFilter` and
`LocalUserFilter` and never `ApiKeyFilter`, so the bearer key is not checked at all, and
the Elasticsearch behind it is commonly run with `xpack.security.enabled=false`. Anything
else on the host that can make an HTTP request therefore has full read *and write* access
to the corpus, and this server's allowlist never sees those requests.

That is documented in the README under "What the allowlist is worth, and what it is not",
and it is the reason the allowlist exists in the first place. Reports that amount to
"`curl` can write to the corpus" describe the situation this project was built for, not a
flaw in it. The fix for that is server-side: run Datashare with `--mode=SERVER` and enable
Elasticsearch security.

Also out of scope: vulnerabilities in ICIJ Datashare or Elasticsearch themselves (report
those to their maintainers), and the contents of `docker-compose.yml`, which describes a
deliberately unauthenticated local development stack bound to loopback.
