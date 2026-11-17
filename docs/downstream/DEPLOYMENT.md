# Sustained A-share paper deployment

[简体中文](DEPLOYMENT_CN.md) · [Using the workspace](WEB_WORKSPACE.md)

This isolated Compose stack runs PostgreSQL, one-shot migration/bootstrap, the official Gunicorn API, the independent paper worker and nginx with the official frontend. It starts no upstream trading worker, automatic strategy evaluator/promotion, AI queue or approval service. Source-level brokerage denial and read-only Agent policy remain enforced. Do not combine this file with the upstream full-stack Compose definition.

## First deployment on Ubuntu

Install Docker Engine with Compose v2 and Python 3.11+. Building the pinned frontend also requires its Node/pnpm toolchain. From the repository root:

```bash
python scripts/frontend/prepare_ashare_frontend.py ../work/quantdinger-frontend --build
```

Copy the complete built `dist/` into an external versioned directory such as `/srv/quantdinger-paper/assets/release-1/`, including its upstream license. Do not bind a development `dist/` that future builds remove and recreate.

```bash
python scripts/ops/paper_stack.py --state-dir /srv/quantdinger-paper/state init \
  --frontend-dist /srv/quantdinger-paper/assets/release-1 \
  --project quantdinger-paper --port 8077
python scripts/ops/paper_stack.py --state-dir /srv/quantdinger-paper/state build
python scripts/ops/paper_stack.py --state-dir /srv/quantdinger-paper/state up
python scripts/ops/paper_stack.py --state-dir /srv/quantdinger-paper/state status
```

Open `http://127.0.0.1:8077/#/ashare-paper`. Initialization generates persistent random administrator/database passwords and encryption secrets in mode-0600 `state/config.env`, within a mode-0700 directory, without printing them. The default username is `paper_admin`. A password changed in the UI is stored in PostgreSQL and is not reset by restart. Existing state directories are never overwritten, and project names already owning containers or volumes are rejected. `--import-env-json /private/local-env.json` can preserve account settings and secrets from the local acceptance setup; this does **not** migrate its database.

Migration and an active administrator must be verified before API/worker startup. Application processes run as UID 10001; database, raw evidence and logs use separate named volumes. Only the localhost web port is published; neither PostgreSQL nor the API has a host port. For remote access, use an SSH tunnel or separately configured TLS/access-controlled reverse proxy.

The official lock deliberately pins security-fixed `filelock==4.2.4` although LiteLLM declares `<4`. The image follows upstream's `--no-deps` installation and permits only this exact documented mismatch in its dependency check; every other mismatch fails. Broker/AI dependencies existing in the official image do not authorize enabling execution.

## Operations and recovery

```bash
python scripts/ops/paper_stack.py --state-dir /srv/quantdinger-paper/state status
python scripts/ops/paper_stack.py --state-dir /srv/quantdinger-paper/state logs
python scripts/ops/paper_stack.py --state-dir /srv/quantdinger-paper/state stop
python scripts/ops/paper_stack.py --state-dir /srv/quantdinger-paper/state up
```

Docker `unless-stopped` restarts failed processes and resumes services after host reboot when Docker is enabled at boot. An intentional `stop` remains stopped until `up`. Gunicorn provides production request handling, timeouts and worker recycling. Nginx resolves Docker DNS dynamically after API container recreation. Docker logs are limited to 3×10 MB per service; application files use the upstream rotating logger.

Worker health reads the durable global heartbeat even when no accounts exist. Liveness is distinct from trading readiness: lunch, market closure and missing human daily review must not trigger restart. Stale quotes and missing rules still deny orders. Docker marks a hung container unhealthy but does **not** restart it solely because of that status. Use monitoring/operator response; no privileged general-purpose autoheal service is installed.

Human daily review remains necessary. Natural multi-day observation takes actual trading days; controlled replay must not be described as elapsed live operation. Calendar coverage and supported-security/fill-model limits remain in the acceptance reports. Monitor disk capacity; evidence is not automatically deleted.

## Consistent backups and isolated restoration

```bash
python scripts/ops/paper_stack.py --state-dir /srv/quantdinger-paper/state backup \
  /srv/quantdinger-paper/backups/manual-001
python scripts/ops/paper_stack.py --state-dir /srv/quantdinger-paper/state restore-check \
  /srv/quantdinger-paper/backups/manual-001
```

Backup briefly stops previously running API/worker services, dumps the whole PostgreSQL database and raw evidence, saves private configuration, source revision, image IDs, ledger/user table fingerprints and a SHA256 manifest, then resumes those services. Before pausing, the running-service list is persisted. SIGTERM unwinds recovery; the timer’s ExecStopPost also recovers after timeout/forced termination, without starting services that were already stopped. Manual recovery is `python scripts/ops/paper_stack.py --state-dir /srv/quantdinger-paper/state resume-backup`. Failure also attempts resumption; incomplete directories remain for investigation and are not valid backups. Backup directories cannot already exist or be inside Git. **Backups contain account data and secrets: filesystem permissions are not encryption; encrypt before storing off-host.** Preserve SECRET_KEY to decrypt existing notification settings.

`restore-check` checks file integrity and archive safety, restores into a new PostgreSQL container with no network and no published port, verifies every saved ledger/user table fingerprint, and removes only its own temporary container/volumes. The original deployment remains untouched.

Actual restoration requires a new state directory and a new Compose project without existing containers or volumes:

```bash
python scripts/ops/paper_stack.py --state-dir /srv/quantdinger-paper/restored-state restore \
  /srv/quantdinger-paper/backups/manual-001 \
  --project quantdinger-paper-restored --port 8078 \
  --frontend-dist /srv/quantdinger-paper/assets/release-1
python scripts/ops/paper_stack.py --state-dir /srv/quantdinger-paper/restored-state up
```

Restore leaves API/worker stopped after verification so the operator can review versions before `up`. Failed targets remain for investigation; retry into another new directory/project. Never use `down -v` on production data. Before upgrades, back up and perform an isolated restore drill, build the new application, use versioned frontend assets and run `up` for migration. Reverting an image alone cannot reverse a database migration.

## Daily backups

```bash
python scripts/ops/paper_stack.py --state-dir /srv/quantdinger-paper/state install-backup-timer \
  --backup-root /srv/quantdinger-paper/backups
systemctl --user list-timers 'quantdinger-backup-*'
```

The private user service/timer runs at 18:10 Asia/Shanghai daily, with up to 60 seconds random delay and missed-run catch-up. An exclusive lock prevents backups from overlapping each other or CLI build/up/stop. Do not bypass the CLI with direct Docker start/stop/update commands during a backup. Existing units are not overwritten. A user systemd manager is required; unattended operation while logged out additionally requires user lingering. Check `loginctl show-user "$USER" -p Linger`; an administrator can enable it with `loginctl enable-linger USERNAME`. Inspect failures through `journalctl --user -u quantdinger-backup-quantdinger-paper.service`. No backups are automatically deleted; manage retention and capacity separately.

Feishu remains optional and one-way. The user configures and explicitly tests the Webhook in the UI. Deploying or backing up never sends a test message or claims delivery when no bot is configured.

See [continuous operations](OPERATIONS.md) for heartbeat, storage protection and daily reports.
