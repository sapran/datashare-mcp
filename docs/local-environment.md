# Local Datashare environment (tenderchad)

A local ICIJ Datashare for the `tenderchad` corpus, run from a prebuilt Docker image (no
host JDK) plus a single Elasticsearch container. Single-user **LOCAL mode**.

It is a **Docker Compose project**, defined by [`docker-compose.yml`](../docker-compose.yml)
at the repository root, exactly as the sibling Aleph stack is. Everything below is
`docker compose` run from the repository root.

This instance runs **alongside that Aleph stack**, which holds ports 8080, 9200 and 5000.
Datashare is therefore published on **8888** (app + UI) and **9201** (its own
Elasticsearch). Only the *host* mapping moves — the containers still listen on 8080 and
9200 internally, and the app reaches Elasticsearch as `http://elasticsearch:9200` over the
project network.

## Topology

| Component | How | Endpoint |
|---|---|---|
| Elasticsearch 8.19.8 | service `elasticsearch` → `datashare-elasticsearch-1` (vol `datashare-es-data`) | http://localhost:9201 |
| Datashare app + UI | service `app` → `datashare-app-1`, image `icij/datashare:21.2.1-ui` | http://localhost:8888 |
| Docker network | `datashare_default`, created by Compose | — |
| Relational state (SQLite) | vol `datashare-dist` → `/home/datashare/dist/datashare.db` (projects, API keys) | — |
| Corpus | `$DATASHARE_DATA_DIR` mounted `:ro` at `/home/datashare/data` | project `tenderchad` |
| MCP | `datashare-mcp` (uv tool), wired via `.mcp.json` | stdio |

- `DATASHARE_DATA_DIR` is **required** — there is no default. It comes from the gitignored
  `.env` beside the compose file, which Compose reads automatically; on this machine it is
  `/Users/user/cab/datashare.ai/data`. Leave it unset and `up` refuses to start with
  `required variable DATASHARE_DATA_DIR is missing a value`, rather than mounting an empty
  directory and serving nothing. The bind also sets `create_host_path: false`, so a value
  pointing at a path that does not exist fails too instead of being created empty.
- Both published ports are bound to **`127.0.0.1` only**, matching how the Aleph stack
  publishes every one of its ports. This is not belt-and-braces: LOCAL mode does **not**
  enforce API-key auth — it binds `CsrfFilter` and `LocalUserFilter`, never
  `ApiKeyFilter` — so anything that can reach 8888 has full read *and write* access to the
  corpus, key or no key. Port 9201 is Elasticsearch with `xpack.security.enabled=false`,
  where the equivalent reach is an unauthenticated `DELETE /tenderchad`. Publishing either
  on `0.0.0.0` would offer both to every network this laptop joins.
- `--bind 0.0.0.0` in the app's `command` is a *different* setting and must stay. It makes
  the server listen on all interfaces **inside its container**, which is what the port
  mapping connects to; LOCAL mode otherwise listens on the container's loopback only and
  the published port reaches nothing.
- The two volumes are declared `external` with an explicit `name:`, so Compose adopts the
  existing ones rather than creating project-prefixed empties, and cannot delete them on
  `down -v`. They are the only copy of the index and of the API-key hash.
- The dashboard searches across the local user's projects, so the default
  **`local-datashare`** project must also exist (see "Register the projects" below) or
  `count-by-project` returns 500.
- LOCAL mode = SQLite + in-memory queues, so **no Postgres/Redis/AMQP/S3-mock/Temporal**.

## Backend source checkout

The `icij/datashare:21.2.1-ui` image is prebuilt and carries both the fat JAR and the
built SPA at `/home/datashare/app`. The source checkout exists only as a reference for
reading route registrations — in particular `IndexResource` and `IndexAccessVerifier`,
which `src/datashare_mcp/readonly.py` cites:

```sh
git clone --depth 1 --branch 21.2.1 https://github.com/ICIJ/datashare.git ~/git/datashare
git -C ~/git/datashare describe --tags     # 21.2.1
```

**Do not build it.** No `make devenv`, no `make install`, no Maven — a build pulls a
1–3 GB `~/.m2` for no gain. No host JDK, Maven, Node or Yarn is required, and
`datashare-client` is not needed either: the `-ui` image already carries the built SPA.

Pin to `21.2.1` rather than a newer release: the `datashare-es-data` volume holds a
`tenderchad` index written by 21.2.1 and `datashare-dist` holds SQLite state written by
it. Reading those with a newer version risks an unrequested schema migration.

## Start

From the repository root:

```sh
docker compose up -d
```

**First run on a machine that has never hosted this stack**, two one-time steps:

```sh
# 1. point at the corpus — required, no default
echo 'DATASHARE_DATA_DIR=/absolute/path/to/corpus' > .env

# 2. create the data volumes. They are declared `external` so that no compose command
#    can delete them, which also means Compose will not create them for you.
docker volume create datashare-es-data
docker volume create datashare-dist
```

Skip step 1 and `up` refuses with `required variable DATASHARE_DATA_DIR is missing a
value`. Skip step 2 and it refuses with `external volume "datashare-es-data" not found`.
Both are deliberate: a loud first-run failure beats a healthy-looking instance serving
nothing. A fresh stack then needs the ingest and project-registration steps below before
it holds anything.

On this machine `.env` exists and both volumes hold the corpus, so `up -d` is all there is
to it.

`app` is gated on `elasticsearch` reporting **healthy**, not merely created, so the two
cannot race:

```
 Container datashare-elasticsearch-1  Started
 Container datashare-elasticsearch-1  Waiting
 Container datashare-elasticsearch-1  Healthy
 Container datashare-app-1            Started
```

Without that gate the app's JVM caches a failed DNS lookup for `elasticsearch` and every
`/api/index/...` call answers HTTP 500 with `UnknownHostException` until the app is
restarted by hand — the reason the healthcheck is there.

Everyday commands:

```sh
docker compose ps                  # status, including health
docker compose logs -f app         # follow the server log
docker compose restart app         # bounce just the app
docker compose down                # stop and remove containers, KEEPING the data
```

The watermark overrides and `indices.id_field_data.enabled=true` in the compose file are
not tuning knobs — the existing index was created under them. `flood_stage=100%` means
Elasticsearch will not go read-only until the disk is completely full, so keep real
headroom on the host rather than relaxing anything there.

Smoke test: `curl -s localhost:8888/version` (API); open http://localhost:8888 (UI).

## (Re-)ingest the corpus

Only needed if the index is lost. `ingest` is a one-shot service behind the `tools`
profile, so `up` never starts it. Stop the app first — two processes must not write the
same SQLite:

```sh
docker compose stop app
docker compose --profile tools run --rm ingest
# the CLI JVM may hang after "exiting main"; once "drained N documents" appears, Ctrl-C it.
docker compose start app
```

The plain `icij/datashare:21.2.1` tag is no longer on this machine; `-ui` is a superset
and runs CLI mode identically, which is why both services use it.

Verify: `curl -s localhost:9201/tenderchad/_count` → **4884**.

## Register the projects (one-time, after first ingest)

CLI ingest creates the ES index but **not** the relational project row. Without it,
`/api/index/...` returns 401 and the UI and MCP see no projects.

```sh
KEY=$(security find-generic-password -s datashare-mcp -a "$USER" -w)
curl -s -H "Authorization: Bearer $KEY" -H "X-DS-CSRF-TOKEN: x" -b "_ds_csrf_token=x" \
  -H "Content-Type: application/json" -X POST http://localhost:8888/api/project/ \
  -d '{"name":"tenderchad","label":"tenderchad","sourcePath":"/home/datashare/data"}'
# also create the default project so the dashboard's cross-project query works:
curl -s -H "Authorization: Bearer $KEY" -H "X-DS-CSRF-TOKEN: x" -b "_ds_csrf_token=x" \
  -H "Content-Type: application/json" -X POST http://localhost:8888/api/project/ \
  -d '{"name":"local-datashare","label":"local-datashare","sourcePath":"/home/datashare/data"}'
```

Both must exist. Verify with:

```sh
curl -s -H "Authorization: Bearer $KEY" localhost:8888/api/project/ | jq -r '.[].name'
# tenderchad
# local-datashare
```

## API key

The key lives in the macOS Keychain under service `datashare-mcp`, not in any file:

```sh
security find-generic-password -s datashare-mcp -a "$USER" -w
```

To mint a fresh one (Datashare cannot retrieve an existing key later). `--createApiKey`
writes the hashed key into the same `datashare.db` the running app holds open, so stop the
app first, exactly as for a re-ingest:

```sh
docker compose stop app
docker compose --profile tools run --rm apikey
# logs: "generated secret key for user local ...: <KEY>"
docker compose start app
security add-generic-password -U -a "$USER" -s datashare-mcp -w   # paste at the prompt
```

This **replaces** the stored key, so only run it if the current one is lost or broken.

The key is printed in **cleartext**. Keep `--rm` so no container log retains it — without
it, `docker compose logs apikey` hands the key to anything that can reach the Docker
socket — clear the terminal scrollback afterwards, and never paste that output into an
issue, a screenshot or a transcript.

## Wiring the MCP

Point the client at port 8888 and read the key from the Keychain rather than storing it:

```json
{
  "mcpServers": {
    "datashare": {
      "command": "/Users/<you>/.local/bin/datashare-mcp",
      "env": {
        "DATASHARE_URL": "http://localhost:8888",
        "DATASHARE_API_KEY": "!security find-generic-password -s datashare-mcp -a \"$USER\" -w"
      }
    }
  }
}
```

Reload the MCP client afterwards. Reinstall after pulling changes:
`uv tool install --force ~/git/datashare-mcp`.

## Troubleshooting

| Symptom | Cause / fix |
|---|---|
| UI unreachable but `docker compose ps` shows `Up` | `--bind 0.0.0.0` missing from the app's `command`. LOCAL mode binds localhost-only *inside* the container, which the port mapping cannot reach. |
| `/api/index/...` → 500 `UnknownHostException` | The app started before Elasticsearch resolved and its JVM cached the failure. Should be impossible via `docker compose up` — the health gate covers it. `docker compose restart app`. |
| `elasticsearch` stuck `starting` in `docker compose ps` | The healthcheck has not passed within `start_period` + retries. `docker compose logs elasticsearch`; an exit 137 there is the kernel OOM-killing it, so free memory on the host. |
| Direct API call → 403 `"CSRF token wrong or missing"` | Non-GET `/api/*` needs a matching `Cookie: _ds_csrf_token=X` and `X-DS-CSRF-TOKEN: X` (any matching pair). The MCP client sends these automatically. |
| `/api/index/...` → 401 | The relational project row is missing. Run the project-registration POSTs above. |
| Dashboard → 500 on `count-by-project` | The `local-datashare` project is missing. Register it too. |
| Search returns 0 hits | An INDEX stage did not run. SCAN alone only enumerates files. |
| Instance has no projects and no documents | The volumes exist but were never populated. Run the ingest and project-registration steps above. Compose cannot have substituted empties — the volumes are `external`, so it would have refused to start instead. |
| `up` → `external volume "datashare-es-data" not found` | First run on this machine. `docker volume create datashare-es-data datashare-dist`, then ingest and register the projects. |
| `up` → `required variable DATASHARE_DATA_DIR is missing a value` | No `.env`. Write `DATASHARE_DATA_DIR=/absolute/path/to/corpus` into it — the file is gitignored, so a fresh clone or a new worktree never has one. |
| Port 8888 or 9201 in use | Something else took the port. Do **not** fall back to 8080/9200 — Aleph owns those, and 8888/9201 are pinned in `plugins/datashare/.mcp.json` and in the live-test command. |
| `get_document_content` → 500 | Stale `datashare-mcp`. The client must probe `maxOffset` with `limit=0` and then fetch the range; Datashare's unranged content route reads the empty relational `document` table. |
| MCP edits not taking effect in Claude Desktop | Quit with Cmd-Q and relaunch. Closing the window keeps the subprocess alive. |

## Stop / teardown

```sh
docker compose stop     # stop, keep containers
docker compose down     # stop and remove containers + network, KEEP the volumes
```

`datashare-es-data` and `datashare-dist` are declared **`external`** in the compose file,
which puts them outside this project's lifecycle. `docker compose down -v` therefore
cannot delete them — verified by running it — and neither can `docker compose rm -v`.
That is deliberate: they are the only copy of the 4884-document index and of the SQLite
project rows and API-key hash, and rebuilding them means a full re-ingest.

What *can* still destroy them is Docker itself, outside Compose:

```sh
docker volume rm datashare-es-data datashare-dist   # DESTRUCTIVE, the only way
docker volume prune -a                              # DESTRUCTIVE while the stack is down
docker system prune --volumes                       # DESTRUCTIVE, same reason
```

A pruned volume is unrecoverable; treat any `prune` on this machine as a deliberate act,
not housekeeping.

### Backups

Two things in this stack cannot be recreated from the repository:

```sh
# the SQLite state — projects and the API-key hash. Stop the app first.
docker compose stop app
docker run --rm -v datashare-dist:/v:ro -v ~/backups:/b alpine \
  tar czf /b/datashare-dist.tgz -C /v .
docker compose start app

# the image, which is a local build from a source tree that no longer exists
docker save icij/datashare:21.2.1-ui | gzip > ~/backups/icij-datashare-21.2.1-ui.tar.gz
```

Restore with `tar xzf` into a fresh volume, and `docker load -i` respectively. The ES
index is deliberately **not** backed up — it is large and reconstructible by re-ingesting
the corpus, which the read-only bind still holds.

Never run `docker system prune -a`, `docker image prune -a`, or
`docker compose down --rmi all` on this machine: each deletes
`icij/datashare:21.2.1-ui`, which is referenced by no container while the stack is down.
Worse than losing it, `pull_policy: never` is what then makes the failure loud — without
it Compose would silently pull a different build of the same tag from Docker Hub and run
it against volumes only 21.2.1 should touch.

## Coexisting with the Aleph stack

Both stacks share one Docker VM. Aleph's eight `ingest-file` workers take roughly 1.3–1.9
GB each while ingesting; Datashare's Elasticsearch is pinned at a 2 GB heap. On an 18 GB
VM the two together can exhaust it, and the symptom is an exit 137 (kernel OOM kill) on
whichever container asks for memory next — or, if the daemon itself is starved, a Docker
Desktop restart that leaves every container without a restart policy stopped.

If memory is tight, stop the ingest workers rather than shrinking the Datashare heap:

```sh
docker stop $(docker ps -q --filter name=aleph-ingest-file)
docker start $(docker ps -aq --filter name=aleph-ingest-file)   # afterwards
```
