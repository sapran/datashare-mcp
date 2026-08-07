## 1. The compose file

- [x] 1.1 Add `docker-compose.yml` with project name `datashare` and services
      `elasticsearch` and `app`, reproducing the current image, ports (8888→8080,
      9201→9200), environment and CLI flags exactly.
- [x] 1.2 Pin the two volumes with an explicit `name:` (`datashare-es-data`,
      `datashare-dist`) so the existing index and SQLite state are adopted rather than
      shadowed by project-prefixed empties.
- [x] 1.3 Give `elasticsearch` a healthcheck and gate `app` on
      `condition: service_healthy`.
- [x] 1.4 Add `ingest` and `apikey` as one-shot services behind Compose profiles, so
      `docker compose up -d` does not start them.
- [x] 1.5 Parameterise the corpus bind as `${DATASHARE_DATA_DIR:-./data}`, read-only.

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
- [x] 3.3 Confirm the data survived: `/version` answers, the `tenderchad` count is still
      4884, and `/api/project/` still lists both projects (proving `datashare-dist` was
      adopted, not recreated).
      *`/version` → 21.2.1; count → 4884; projects → `local-datashare`, `tenderchad`. The
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

## 4. Documentation

- [x] 4.1 Rewrite `docs/local-environment.md` around `docker compose`: Start, re-ingest,
      API key, project registration, troubleshooting and teardown.
- [x] 4.2 State plainly that `docker compose down -v` destroys the index and the API key,
      and that `docker compose down` is the safe teardown.

## 5. Not executed

- [ ] 5.1 `docker compose --profile tools run --rm ingest` — validated structurally
      (`docker compose --profile tools config` resolves the right image, command and
      volumes, and `up` does not start it) but **not run**: it re-indexes all 4884
      documents and requires stopping the app.
- [ ] 5.2 `docker compose --profile tools run --rm apikey` — same structural validation,
      **not run**: `--createApiKey local` replaces the credential currently stored in the
      login Keychain.
