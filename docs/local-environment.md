# Local Datashare environment (tenderchad)

A local ICIJ Datashare for the `tenderchad` corpus, run from a prebuilt Docker image (no
host JDK) plus a single Elasticsearch container. Single-user **LOCAL mode**.

This instance runs **alongside a local Aleph stack**, which holds ports 8080, 9200 and
5000. Datashare is therefore published on **8888** (app + UI) and **9201** (its own
Elasticsearch). Only the *host* mapping moves — the containers still listen on 8080 and
9200 internally, and the app reaches ES by container name over `datashare-net`, so
`--elasticsearchAddress http://datashare-es:9200` is unchanged.

## Topology

| Component | How | Endpoint |
|---|---|---|
| Elasticsearch 8.19.8 | container `datashare-es` (named vol `datashare-es-data`) | http://localhost:9201 |
| Datashare app + UI | container `datashare`, image `icij/datashare:21.2.1-ui` | http://localhost:8888 |
| Docker network | `datashare-net` (app↔ES by container name) | — |
| Relational state (SQLite) | named vol `datashare-dist` → `/home/datashare/dist/datashare.db` (projects, API keys) | — |
| Corpus | host `~/cab/datashare.ai/data` mounted `:ro` at `/home/datashare/data` | project `tenderchad` |
| MCP | `datashare-mcp` (uv tool), wired via `.mcp.json` | stdio |

- The dashboard searches across the local user's projects, so the default
  **`local-datashare`** project must also exist (see "Register the projects" below) or
  `count-by-project` returns 500.
- LOCAL mode = SQLite + in-memory queues, so **no Postgres/Redis/AMQP/S3-mock/Temporal**.
- LOCAL mode does **not** enforce API-key auth (only `LocalUserFilter`); the key is for
  completeness and for a future SERVER mode.

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

```sh
# 1. network + Elasticsearch
docker network create datashare-net
docker run -d --name datashare-es --network datashare-net -p 9201:9200 \
  -e "ES_JAVA_OPTS=-Xms2g -Xmx2g" -e discovery.type=single-node -e cluster.name=datashare \
  -e cluster.routing.allocation.disk.watermark.low=90% \
  -e cluster.routing.allocation.disk.watermark.high=99% \
  -e cluster.routing.allocation.disk.watermark.flood_stage=100% \
  -e xpack.security.enabled=false -e indices.id_field_data.enabled=true \
  -v datashare-es-data:/usr/share/elasticsearch/data \
  docker.elastic.co/elasticsearch/elasticsearch:8.19.8

# 2. app server (LOCAL). --bind 0.0.0.0 is REQUIRED (LOCAL defaults to localhost-only).
docker run -d --name datashare --network datashare-net -p 8888:8080 \
  -e DATASHARE_SYNC_NLP_MODELS=false -e DS_JAVA_OPTS=-Xmx2g \
  -v datashare-dist:/home/datashare/dist \
  -v /Users/user/cab/datashare.ai/data:/home/datashare/data:ro \
  icij/datashare:21.2.1-ui \
  --mode LOCAL --bind 0.0.0.0 --dataDir /home/datashare/data \
  --defaultProject tenderchad --elasticsearchAddress http://datashare-es:9200
```

The watermark overrides and `indices.id_field_data.enabled=true` are not tuning knobs —
the existing index was created under them. `flood_stage=100%` means Elasticsearch will
not go read-only until the disk is completely full, so keep real headroom on the host
rather than relaxing anything here.

Smoke test: `curl -s localhost:8888/version` (API); open http://localhost:8888 (UI).

## (Re-)ingest the corpus

Only needed if the index is lost. Stop the server first — two processes must not write
the same SQLite — then run a one-shot CLI container sharing the same volumes and ES:

```sh
docker stop datashare
docker run --rm --network datashare-net \
  -e DATASHARE_SYNC_NLP_MODELS=false -e DS_JAVA_OPTS=-Xmx3g \
  -v datashare-dist:/home/datashare/dist \
  -v /Users/user/cab/datashare.ai/data:/home/datashare/data:ro \
  icij/datashare:21.2.1-ui \
  --mode CLI --dataDir /home/datashare/data --defaultProject tenderchad \
  --stages "SCAN,INDEX" --elasticsearchAddress http://datashare-es:9200
# the CLI JVM may hang after "exiting main"; once "drained N documents" appears, docker stop it.
```

The plain `icij/datashare:21.2.1` tag is no longer on this machine; `-ui` is a superset
and runs CLI mode identically.

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

To mint a fresh one (Datashare cannot retrieve an existing key later):

```sh
docker run --rm --network datashare-net -v datashare-dist:/home/datashare/dist \
  icij/datashare:21.2.1-ui --mode CLI --createApiKey local \
  --elasticsearchAddress http://datashare-es:9200
# logs: "generated secret key for user local ...: <KEY>"
security add-generic-password -U -a "$USER" -s datashare-mcp -w   # paste at the prompt
```

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
| UI unreachable but `docker ps` shows `Up` | `--bind 0.0.0.0` missing. LOCAL mode binds localhost-only *inside* the container, which the port mapping cannot reach. |
| Direct API call → 403 `"CSRF token wrong or missing"` | Non-GET `/api/*` needs a matching `Cookie: _ds_csrf_token=X` and `X-DS-CSRF-TOKEN: X` (any matching pair). The MCP client sends these automatically. |
| `/api/index/...` → 401 | The relational project row is missing. Run the project-registration POSTs above. |
| Dashboard → 500 on `count-by-project` | The `local-datashare` project is missing. Register it too. |
| Search returns 0 hits | An INDEX stage did not run. SCAN alone only enumerates files. |
| Port 8888 or 9201 in use | Something else took the port. Do **not** fall back to 8080/9200 — Aleph owns those, and 8888/9201 are pinned in `plugins/datashare/.mcp.json` and in the live-test command. |
| `get_document_content` → 500 | Stale `datashare-mcp`. The client must probe `maxOffset` with `limit=0` and then fetch the range; Datashare's unranged content route reads the empty relational `document` table. |
| MCP edits not taking effect in Claude Desktop | Quit with Cmd-Q and relaunch. Closing the window keeps the subprocess alive. |

## Stop / teardown

```sh
docker stop datashare datashare-es                  # stop (keeps data)
docker rm -f datashare datashare-es                 # remove containers
docker volume rm datashare-dist datashare-es-data   # DROP the index + DB (destructive)
docker network rm datashare-net
```

The two volumes are the only copy of the 4884-document index and the API-key hash. Never
`docker volume rm` them as a cleanup step. Likewise never run `docker system prune -a` or
`docker image prune -a` on this machine: `icij/datashare:21.2.1-ui` is referenced by no
container while stopped, was built from a source tree that no longer exists locally, and
an "unused images" prune deletes it. A copy is archived at
`~/backups/icij-datashare-21.2.1-ui.tar.gz` (`docker load -i` to restore).
