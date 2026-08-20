## 1. The compose file

- [x] 1.1 Add `docker-compose.yml` with project name `datashare` and services
      `elasticsearch` and `app`, reproducing the current image, environment and CLI flags
      exactly, on the same host ports 8888 and 9201.
      *Amended in review: the ports are published on `127.0.0.1` rather than `0.0.0.0`.
      The prior `-p` form bound every interface, exposing an auth-free Datashare and an
      auth-free Elasticsearch to any network the laptop joins; the Aleph stack this
      imitates loopback-pins every port. Deliberate deviation from "exactly".*
- [x] 1.2 Pin the two volumes with an explicit `name:` (`datashare-es-data`,
      `datashare-dist`) so the existing index and SQLite state are adopted rather than
      shadowed by project-prefixed empties.
      *Amended in review: also declared `external: true`. `name:` renames a volume, it
      does not disown it, so `docker compose down -v` would still have deleted both.*
- [x] 1.3 Give `elasticsearch` a healthcheck and gate `app` on
      `condition: service_healthy`.
      *Probe uses `?wait_for_status=yellow`: a bare `/_cluster/health` answers 200 on a
      RED cluster too, which would have let the app start against an unusable index.*
- [x] 1.4 Add `ingest` and `apikey` as one-shot services behind Compose profiles, so
      `docker compose up -d` does not start them.
- [x] 1.5 Parameterise the corpus bind as `DATASHARE_DATA_DIR`, read-only.
      *Amended in review from `${...:-./data}` to `${...:?}` plus long-syntax
      `create_host_path: false`, so a missing or wrong path fails instead of silently
      mounting an empty directory.*
- [x] 1.6 Added in review: `pull_policy: never` on the three Datashare services. The
      image is a local build that cannot be rebuilt, but `icij/datashare` is also a public
      Docker Hub repository, so a lost local image would otherwise be silently replaced by
      a different upstream build of the same tag.
- [x] 1.7 Added in review: `restart: on-failure` and `mem_limit: 3g` on both long-running
      services, matching Aleph. A Docker Desktop restart had left the policy-less
      containers stopped, and an unbounded container had been OOM-killed.

## 2. Local configuration

- [x] 2.1 Recreate the gitignored `.env` in the main checkout — it was written in the
      previous change's worktree and removed with it — and add `DATASHARE_DATA_DIR` for
      this machine. Note in it that Compose reads this file for variable substitution,
      correcting the "not auto-loaded" claim.

## 3. Cutover and verification

Needs Docker and the existing volumes; a contributor without them can only do section 1.

- [x] 3.1 Validate the rendered configuration with `docker compose config` before starting
      anything, and confirm the volume and bind sources are the existing ones.
- [x] 3.2 Remove the two `docker run` containers and bring the stack up with
      `docker compose up -d`.
- [x] 3.3 Confirm the data survived: `/version` answers, the corpus count is still
      4884, and `/api/project/` still lists both projects (proving `datashare-dist` was
      adopted, not recreated).
      *`/version` → 21.2.1; count → 4884; projects → both registered rows. The
      `datashare.db` md5 is `d892bc3e…` both before and after the cutover, and
      `docker volume ls` shows no project-prefixed volume was created.*
- [x] 3.4 Run the live suite against the composed instance.
      *4 passed.*
- [x] 3.5 Confirm the health gate works by bringing the stack up from cold and checking
      the app never logs `UnknownHostException`.
      *`docker compose down` then `up -d`: the log shows Started → Waiting → Healthy →
      app Started, 10.9 s total, and `grep -c UnknownHostException` on the app log is 0.
      `down` without `-v` left both volumes in place.*
- [x] 3.6 Retire the now-unused `datashare-net` network.
- [x] 3.7 Added in review: prove `external: true` by running the destructive command.
      *`docker compose down -v` against the live stack. Both volumes survived and
      `datashare.db` md5 is still `d892bc3e7a41653195a9e5d64529d105`. Brought back up:
      count 4884, both projects, live suite 4 passed.*
- [x] 3.8 Added in review: confirm the loopback pins and the hardening on the live
      containers.
      *`docker ps` shows `127.0.0.1:8888->8080` and `127.0.0.1:9201->9200`; `docker
      inspect` shows `restart=on-failure` and `mem=3221225472` on both. Live suite still
      4 passed. Also confirmed an unset `DATASHARE_DATA_DIR` aborts with
      `required variable DATASHARE_DATA_DIR is missing a value`.*
- [x] 3.9 Added in review: take the first backup of `datashare-dist`.
      *`~/backups/datashare-dist.tgz`, 16 KB. There was previously no data backup at all,
      only the image tarball.*

## 4. Documentation

- [x] 4.1 Rewrite `docs/local-environment.md` around `docker compose`: Start, re-ingest,
      API key, project registration, troubleshooting and teardown.
- [x] 4.2 State plainly that the two volumes are `external`, so `docker compose down -v`
      cannot delete them and only `docker volume rm` / `docker ... prune` can, and
      document the one-time `docker volume create` and `.env` steps a machine that has
      never run the stack needs.
      *Superseded the original wording, which said `down -v` destroys the data — true of
      the first draft, false of what shipped.*
- [x] 4.3 Added in review: give the API-key procedure the `docker compose stop app` guard
      the compose file declares mandatory for both one-shot services, and warn that the
      key is printed in cleartext.

## 5. Not executed

- [ ] 5.1 `docker compose --profile tools run --rm ingest` — validated structurally
      (`docker compose --profile tools config` resolves the right image, command and
      volumes, and `up` does not start it) but **not run**: it re-indexes all 4884
      documents and requires stopping the app.
- [ ] 5.2 `docker compose --profile tools run --rm apikey` — same structural validation,
      **not run**: `--createApiKey local` replaces the credential currently stored in the
      login Keychain.
