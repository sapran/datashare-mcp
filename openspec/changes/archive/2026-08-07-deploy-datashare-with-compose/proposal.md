# Deploy the local Datashare with Docker Compose

## Why

The local Datashare instance the live test suite runs against is started by two long
`docker run` invocations copied out of `docs/local-environment.md`. The sibling Aleph
stack on the same machine is a `docker compose` project, so the two are managed
differently for no reason.

The `docker run` form has cost real time already:

- **Nothing sequences the two containers.** They are started back to back, and the app's
  JVM caches the failed DNS lookup when Elasticsearch is not up yet. When the ES container
  was OOM-killed, every `/api/index/...` call answered HTTP 500 with
  `UnknownHostException: datashare-es` and the app had to be restarted by hand even after
  ES came back.
- **The commands are only as correct as the copy-paste.** Two ports, two volumes, six
  Elasticsearch settings and six CLI flags have to be reproduced exactly, and the
  watermark settings in particular must match what the existing index was created under.
- **The one-shot operations are undocumented shell.** Re-ingesting the corpus and minting
  an API key are two more bespoke `docker run` lines that share the same volumes and
  network and must be kept in sync with the server's by hand.

## What Changes

- **New `docker-compose.yml`** at the repository root, defining `elasticsearch` and `app`
  under the project name `datashare`, plus `ingest` and `apikey` as profile-gated one-shot
  services.
- The app now waits for Elasticsearch to be **healthy**, not merely created, via a
  healthcheck and `depends_on: condition: service_healthy`. This removes the startup race
  above.
- The corpus path becomes `DATASHARE_DATA_DIR`, a **required** variable, so the compose
  file carries no absolute path belonging to one machine and an unset value fails loudly
  instead of mounting an empty directory.
- `docs/local-environment.md` is rewritten around `docker compose` commands.
- The bespoke `datashare-net` network is retired; Compose's project network replaces it,
  and the one-shot services join it automatically.

No **BREAKING** change to the MCP server: it is not touched, and the instance it talks to
runs the same image, the same volumes and the same flags. Three deliberate differences,
all introduced by review:

1. **Published ports move from `0.0.0.0` to `127.0.0.1`.** The prior `-p 8888:8080` bound
   every interface. Datashare's LOCAL mode never binds `ApiKeyFilter`, so port 8888 was
   unauthenticated read *and write* access to the corpus from any network the laptop
   joined, and 9201 was Elasticsearch with `xpack.security.enabled=false`. The Aleph stack
   this imitates loopback-pins every published port, including its own Elasticsearch.
2. **`--elasticsearchAddress` becomes `http://elasticsearch:9200`**, following the service
   rename.
3. **The app is gated on Elasticsearch being healthy**, as above.

Container names change from `datashare` / `datashare-es` to `datashare-app-1` /
`datashare-elasticsearch-1`, matching the `aleph-api-1` / `aleph-elasticsearch-1` pattern
of the sibling stack. `docker logs datashare` becomes `docker compose logs app`.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

None. This changes how the *dependency under test* is started, not what the MCP server
does. `.openspec.yaml` sets `skip_specs: true` accordingly.

## Impact

- Adds `docker-compose.yml`; rewrites `docs/local-environment.md`.
- **The two existing named volumes must be adopted, not recreated.** `datashare-es-data`
  holds the 4884-document `tenderchad` index and `datashare-dist` holds the SQLite project
  rows and the API-key hash. Compose prefixes volume names with the project name by
  default, which would silently create two empty volumes and present an instance with no
  data. Both are therefore pinned with an explicit `name:` **and** declared
  `external: true`.
- `external: true` is the load-bearing half, added in review. `name:` renames a volume; it
  does not disown it, so `docker compose down -v` — one stray flag on the teardown command
  the new docs recommend routinely — would still have deleted both. External volumes are
  outside the project lifecycle and no compose command can remove them. Verified by
  running `down -v` against the live stack: both volumes and the `datashare.db` digest
  survived.
- **New one-time step on a machine that has never run the stack:**
  `docker volume create datashare-es-data datashare-dist`, plus a `.env` setting
  `DATASHARE_DATA_DIR`. Both omissions now abort `up` with a self-explaining error rather
  than starting an empty-looking instance.
- No dependency, packaging or CI impact. The live test suite's URL and port are unchanged.
