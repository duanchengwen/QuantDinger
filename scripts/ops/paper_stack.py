#!/usr/bin/env python3
"""Operate the isolated paper stack; never print secrets or overwrite a restore target."""
from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
import re
import secrets
import shutil
import signal
import subprocess
import sys
import tarfile
import tempfile
import time
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
COMPOSE = ROOT / 'deploy/ashare/compose.yml'
LEGACY_BACKUP_FILES = {'database.dump', 'evidence.tar.gz', 'config.env', 'ledger.json'}
ALLOWED_BACKUP_FILES = LEGACY_BACKUP_FILES | {'settings.tar.gz'}


def run(args, **kwargs):
    return subprocess.run(args, check=True, **kwargs)


def private_write(path, text):
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, 'w') as stream:
        stream.write(text)


def env_value(value):
    value = str(value)
    if '\n' in value or '\r' in value:
        raise ValueError('Environment values cannot contain line breaks')
    return json.dumps(value, ensure_ascii=False).replace("$", "$$")


def compose(state, *args):
    metadata = json.loads((state / 'deployment.json').read_text())
    return ['docker', 'compose', '--project-name', metadata['project'], '--env-file', str(state / 'config.env'), '-f', str(COMPOSE), *args]


def check_project(project):
    if not re.fullmatch(r'[a-z0-9][a-z0-9_-]{0,48}', project):
        raise ValueError('Use a lowercase alphanumeric Compose project name')


def ensure_new_project(project):
    check_project(project)
    existing = subprocess.check_output(['docker', 'ps', '-aq', '--filter', 'label=com.docker.compose.project=' + project], text=True).strip()
    volumes = subprocess.check_output(['docker', 'volume', 'ls', '-q', '--filter', 'label=com.docker.compose.project=' + project], text=True).strip()
    if existing or volumes:
        raise ValueError('Project already owns containers or volumes; choose a new name')


def init(state, args):
    if state.exists():
        raise ValueError('State directory already exists; refusing to replace secrets')
    ensure_new_project(args.project)
    dist = Path(args.frontend_dist).expanduser().resolve()
    if not (dist / 'index.html').is_file():
        raise ValueError('Build the pinned official frontend first; index.html is missing')
    cfg = {
        'ASHARE_HTTP_PORT': str(args.port), 'ASHARE_FRONTEND_DIST': str(dist),
        'ASHARE_APP_IMAGE': 'quantdinger-ashare:local', 'ASHARE_POSTGRES_IMAGE': 'postgres:18@sha256:2ea8630df176ad090ec92136dee850397c5af8781e4bcf2e4162593691c61bbc',
        'ASHARE_NGINX_IMAGE': 'nginx:1.30.6-alpine3.24-slim', 'ADMIN_USER': 'paper_admin',
        'ADMIN_PASSWORD': 'Qd' + secrets.token_urlsafe(24) + '9!', 'ADMIN_EMAIL': 'admin@example.com',
        'POSTGRES_PASSWORD': secrets.token_hex(24), 'SECRET_KEY': secrets.token_hex(32),
        'CREDENTIAL_ENCRYPTION_KEY': secrets.token_hex(32),
    }
    if args.import_env_json:
        imported = json.loads(Path(args.import_env_json).read_text())
        for key in ('ADMIN_USER', 'ADMIN_PASSWORD', 'ADMIN_EMAIL', 'SECRET_KEY', 'CREDENTIAL_ENCRYPTION_KEY'):
            if imported.get(key):
                cfg[key] = imported[key]
    if len(cfg['SECRET_KEY']) < 32:
        raise ValueError('Imported SECRET_KEY must have at least 32 characters')
    if not 1 <= args.port <= 65535:
        raise ValueError('Invalid port')
    state.mkdir(parents=True, mode=0o700)
    private_write(state / 'config.env', ''.join(f'{key}={env_value(value)}\n' for key, value in cfg.items()))
    private_write(state / 'deployment.json', json.dumps({'project': args.project, 'created_at': datetime.now(UTC).isoformat()}, indent=2) + '\n')
    run(compose(state, 'config', '--quiet'))
    print(f'Initialized private configuration at {state}; credentials are in config.env, not printed.')


def pipe_to_file(args, path):
    with path.open('xb') as output:
        os.chmod(path, 0o600)
        run(args, stdout=output)


def sha256(path):
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def fingerprint(psql):
    query = "SELECT table_name FROM information_schema.tables WHERE table_schema='public' AND (table_name LIKE 'qd_ashare_%' OR table_name IN ('qd_users','qd_user_client_sessions','qd_user_sessions','qd_client_sessions')) ORDER BY table_name"
    tables = subprocess.check_output([*psql, '-At', '-c', query], text=True).splitlines()
    result = {}
    for table in tables:
        if not re.fullmatch(r'qd_[a-z0-9_]+', table):
            raise ValueError('Unexpected table name')
        query = f'COPY (SELECT row_to_json(t)::text AS row FROM "{table}" t ORDER BY 1) TO STDOUT'
        # Capture only checksums in the report, never credentials or account rows.
        data = subprocess.check_output([*psql, '-At', '-c', query])
        result[table] = {'rows': data.count(b'\n'), 'sha256': hashlib.sha256(data).hexdigest()}
    return result


def backup(state, destination):
    destination = destination.expanduser().resolve()
    if destination == ROOT or ROOT in destination.parents:
        raise ValueError('Backups contain secrets and must stay outside the Git checkout')
    destination.mkdir(parents=True, exist_ok=False, mode=0o700)
    running = subprocess.check_output(compose(state, 'ps', '--status', 'running', '--services'), text=True).splitlines()
    paused = [name for name in ('api', 'worker') if name in running]
    intent = state / '.backup-paused.json'
    if intent.exists():
        raise RuntimeError('Unfinished backup recovery exists; run resume-backup first')
    if paused:
        private_write(intent, json.dumps({'services': paused, 'created_at': datetime.now(UTC).isoformat()}) + '\n')
    try:
        if paused:
            run(compose(state, 'stop', *paused))
        pipe_to_file(compose(state, 'exec', '-T', 'db', 'pg_dump', '-U', 'paper', '-d', 'paper', '-Fc', '--no-owner', '--no-acl'), destination / 'database.dump')
        pipe_to_file(compose(state, 'run', '--rm', '--no-deps', '-T', '--entrypoint', 'tar', 'api', '-C', '/app/data', '-czf', '-', '.'), destination / 'evidence.tar.gz')
        pipe_to_file(compose(state, 'run', '--rm', '--no-deps', '-T', '--entrypoint', 'tar', 'api', '-C', '/app/settings', '-czf', '-', '.'), destination / 'settings.tar.gz')
        shutil.copyfile(state / 'config.env', destination / 'config.env')
        os.chmod(destination / 'config.env', 0o600)
        summary = fingerprint(compose(state, 'exec', '-T', 'db', 'psql', '-U', 'paper', '-d', 'paper'))
        private_write(destination / 'ledger.json', json.dumps(summary, indent=2) + '\n')
        manifest = {'created_at': datetime.now(UTC).isoformat(), 'project': json.loads((state / 'deployment.json').read_text())['project'], 'source_revision': subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip(), 'source_dirty': bool(subprocess.check_output(['git', 'status', '--porcelain'], cwd=ROOT, text=True).strip()), 'image_ids': subprocess.check_output(compose(state, 'images', '-q'), text=True).splitlines(), 'files': {name: sha256(destination / name) for name in sorted(ALLOWED_BACKUP_FILES)}}
        private_write(destination / 'manifest.json', json.dumps(manifest, indent=2) + '\n')
        print(f'Backup completed: {destination}. Contains secrets: encrypt before off-host storage.')
    finally:
        resume_backup(state)



def resume_backup(state):
    intent = state / '.backup-paused.json'
    if not intent.exists():
        return
    services = json.loads(intent.read_text())['services']
    if not services or not isinstance(services, list) or any(item not in ('api', 'worker') for item in services):
        raise ValueError('Invalid backup recovery intent')
    last_error = None
    for attempt in range(12):
        try:
            run(compose(state, 'start', *services))
            intent.unlink()
            return
        except subprocess.CalledProcessError as exc:
            last_error = exc
            if attempt < 11:
                time.sleep(5)
    raise RuntimeError('Could not resume paused services; recovery intent retained, run resume-backup') from last_error


def stop_on_signal(signum, _frame):
    # Raising ensures ordinary termination executes the backup's finally clause.
    raise SystemExit(128 + signum)


def verify_backup(source):
    manifest = json.loads((source / 'manifest.json').read_text())
    if set(manifest['files']) not in (ALLOWED_BACKUP_FILES, LEGACY_BACKUP_FILES):
        raise ValueError('Unexpected backup manifest contents')
    for name, digest in manifest['files'].items():
        path = source / name
        if path.is_symlink() or sha256(path) != digest:
            raise ValueError('Backup checksum verification failed: ' + name)
    for name in ('evidence.tar.gz', 'settings.tar.gz'):
        if name not in manifest['files']:
            continue
        with tarfile.open(source / name, 'r:gz') as archive:
            for member in archive:
                if member.name.startswith('/') or '..' in Path(member.name).parts or not (member.isfile() or member.isdir()):
                    raise ValueError('Unsafe evidence/settings archive member')
    return manifest


def wait_database(container, deadline=60):
    end = time.monotonic() + deadline
    while time.monotonic() < end:
        # The image's temporary initialization server is socket-only and restarts.
        result = subprocess.run(['docker', 'exec', container, 'pg_isready', '-h', '127.0.0.1', '-U', 'paper', '-d', 'paper'], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)
        if result.returncode == 0:
            return
        time.sleep(1)
    raise RuntimeError('Isolated restore database did not become ready')


def restore_check(source, image):
    verify_backup(source)
    name = 'ashare-restore-check-' + secrets.token_hex(6)
    with tempfile.TemporaryDirectory(prefix='ashare-restore-') as temporary:
        envfile = Path(temporary) / 'postgres.env'
        private_write(envfile, 'POSTGRES_USER=paper\nPOSTGRES_DB=paper\nPOSTGRES_PASSWORD=' + secrets.token_hex(24) + '\n')
        try:
            run(['docker', 'run', '-d', '--name', name, '--network', 'none', '--env-file', str(envfile), image], stdout=subprocess.DEVNULL)
            wait_database(name)
            with (source / 'database.dump').open('rb') as stream:
                run(['docker', 'exec', '-i', name, 'pg_restore', '-U', 'paper', '-d', 'paper', '--no-owner', '--no-acl', '--exit-on-error'], stdin=stream)
            expected = json.loads((source / 'ledger.json').read_text())
            actual = fingerprint(['docker', 'exec', name, 'psql', '-U', 'paper', '-d', 'paper'])
            if not expected or actual != expected:
                raise RuntimeError('Restored ledger/user fingerprints do not match')
            print(json.dumps({'restore': 'passed', 'isolated': True, 'tables_verified': len(actual), 'evidence_checksum': 'passed'}))
        finally:
            subprocess.run(['docker', 'rm', '-f', '-v', name], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)


def restore(state, source, args):
    manifest = verify_backup(source)
    if state.exists():
        raise ValueError('Restore requires a NEW state directory; existing deployments are never overwritten')
    ensure_new_project(args.project)
    dist = Path(args.frontend_dist).expanduser().resolve()
    if not (dist / 'index.html').is_file():
        raise ValueError('Prepared official frontend dist is required')
    if not 1 <= args.port <= 65535:
        raise ValueError('Invalid port')
    content = (source / 'config.env').read_text()
    content = re.sub(r'^ASHARE_FRONTEND_DIST=.*$', lambda _: 'ASHARE_FRONTEND_DIST=' + env_value(dist), content, flags=re.MULTILINE)
    content = re.sub(r'^ASHARE_HTTP_PORT=.*$', lambda _: 'ASHARE_HTTP_PORT=' + env_value(args.port), content, flags=re.MULTILINE)
    for key, image in (
        ('ASHARE_APP_IMAGE', getattr(args, 'app_image', None)),
        ('ASHARE_POSTGRES_IMAGE', getattr(args, 'postgres_image', None)),
    ):
        if image is not None:
            content, count = re.subn(rf'^{key}=.*$', lambda _: key + '=' + env_value(image), content, flags=re.MULTILINE)
            if count != 1:
                raise ValueError('Restore source has an unexpected image configuration')
    state.mkdir(parents=True, mode=0o700)
    with operation_lock(state):
        private_write(state / 'config.env', content)
        private_write(state / 'deployment.json', json.dumps({'project': args.project, 'restored_from': str(source)}, indent=2) + '\n')
        run(compose(state, 'config', '--quiet'))
        run(compose(state, 'up', '-d', '--wait', 'db'))
        with (source / 'database.dump').open('rb') as stream:
            run(compose(state, 'exec', '-T', 'db', 'pg_restore', '-U', 'paper', '-d', 'paper', '--no-owner', '--no-acl', '--exit-on-error'), stdin=stream)
        with (source / 'evidence.tar.gz').open('rb') as stream:
            run(compose(state, 'run', '--rm', '--no-deps', '-T', '--entrypoint', 'tar', 'api', '-C', '/app/data', '-xzf', '-', '--no-same-owner'), stdin=stream)
        if 'settings.tar.gz' in manifest['files']:
            with (source / 'settings.tar.gz').open('rb') as stream:
                run(compose(state, 'run', '--rm', '--no-deps', '-T', '--entrypoint', 'tar', 'api', '-C', '/app/settings', '-xzf', '-', '--no-same-owner'), stdin=stream)
        actual = fingerprint(compose(state, 'exec', '-T', 'db', 'psql', '-U', 'paper', '-d', 'paper'))
        if actual != json.loads((source / 'ledger.json').read_text()):
            raise RuntimeError('Restore verification failed; services remain stopped')
        print('Restored and verified into a new project; API and worker remain stopped. Review then run up.')



@contextmanager
def operation_lock(state, *, wait_seconds=0):
    with (state / '.backup.lock').open('a') as stream:
        os.chmod(state / '.backup.lock', 0o600)
        deadline = time.monotonic() + wait_seconds if wait_seconds > 0 else None
        while True:
            try:
                fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError as exc:
                remaining = deadline - time.monotonic() if deadline is not None else 0
                if remaining <= 0:
                    raise RuntimeError('Another deployment operation is already running') from exc
                time.sleep(min(remaining, 0.25))
        try:
            yield
        finally:
            fcntl.flock(stream, fcntl.LOCK_UN)


def systemd_argument(value):
    text = str(value)
    if '\n' in text or '\r' in text:
        raise ValueError('Invalid service argument')
    return '"' + text.replace('\\', '\\\\').replace('"', '\\"').replace('%', '%%').replace('$', '$$') + '"'


def install_backup_timer(state, backup_root):
    backup_root = backup_root.expanduser().resolve()
    if backup_root == ROOT or ROOT in backup_root.parents:
        raise ValueError('Backups must stay outside the Git checkout')
    project = json.loads((state / 'deployment.json').read_text())['project']
    check_project(project)
    unit_name = 'quantdinger-backup-' + project
    directory = Path.home() / '.config/systemd/user'
    directory.mkdir(parents=True, exist_ok=True)
    base_command = [sys.executable, Path(__file__).resolve(), '--state-dir', state]
    command = ' '.join(systemd_argument(value) for value in [*base_command, 'backup-nightly', '--backup-root', backup_root])
    recovery = ' '.join(systemd_argument(value) for value in [*base_command, 'resume-backup'])
    service = f"""[Unit]
Description=Consistent paper database and evidence backup ({project})
ConditionPathExists={state / 'config.env'}

[Service]
Type=oneshot
UMask=0077
TimeoutStartSec=10min
TimeoutStopSec=90s
ExecStart={command}
ExecStopPost={recovery}
"""
    timer = f"""[Unit]
Description=Daily paper backup at 18:10 Asia/Shanghai ({project})

[Timer]
OnCalendar=*-*-* 18:10:00 Asia/Shanghai
Persistent=true
RandomizedDelaySec=60
Unit={unit_name}.service

[Install]
WantedBy=timers.target
"""
    if any((directory / (unit_name + suffix)).exists() for suffix in ('.service', '.timer')):
        raise ValueError('Backup service/timer already exists; refusing to overwrite')
    private_write(directory / (unit_name + '.service'), service)
    private_write(directory / (unit_name + '.timer'), timer)
    run(['systemctl', '--user', 'daemon-reload'])
    run(['systemctl', '--user', 'enable', '--now', unit_name + '.timer'])
    print(f'Installed {unit_name}.timer; enable user lingering for operation while logged out.')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--state-dir', type=Path, required=True, help='Private directory outside the Git checkout')
    sub = parser.add_subparsers(dest='command', required=True)
    initialize = sub.add_parser('init')
    initialize.add_argument('--frontend-dist', required=True)
    initialize.add_argument('--project', default='quantdinger-paper')
    initialize.add_argument('--port', type=int, default=8077)
    initialize.add_argument('--import-env-json', help='Optional existing local JSON config; preserves account secrets only')
    for name in ('build', 'up', 'status', 'stop', 'logs', 'resume-backup'):
        sub.add_parser(name)
    nightly = sub.add_parser('backup-nightly')
    nightly.add_argument('--backup-root', type=Path, required=True)
    timer = sub.add_parser('install-backup-timer')
    timer.add_argument('--backup-root', type=Path, required=True)
    save = sub.add_parser('backup')
    save.add_argument('destination', type=Path)
    check = sub.add_parser('restore-check')
    check.add_argument('source', type=Path)
    check.add_argument('--postgres-image', default='postgres:18')
    recover = sub.add_parser('restore')
    recover.add_argument('source', type=Path)
    recover.add_argument('--project', required=True)
    recover.add_argument('--frontend-dist', required=True)
    recover.add_argument('--port', type=int, default=8078)
    recover.add_argument('--app-image', help='Use a preloaded, verified paper application image')
    recover.add_argument('--postgres-image', help='Use a preloaded, verified PostgreSQL image')
    args = parser.parse_args()
    state = args.state_dir.expanduser().resolve()
    if state == ROOT or ROOT in state.parents:
        parser.error('Keep secrets and backups outside the Git checkout')
    if args.command == 'init':
        init(state, args)
    elif args.command == 'resume-backup':
        with operation_lock(state):
            resume_backup(state)
    elif args.command == 'backup':
        with operation_lock(state):
            backup(state, args.destination)
    elif args.command == 'backup-nightly':
        destination = args.backup_root / (datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ-') + secrets.token_hex(3))
        with operation_lock(state):
            backup(state, destination)
    elif args.command == 'install-backup-timer':
        install_backup_timer(state, args.backup_root)
    elif args.command == 'restore-check':
        restore_check(args.source.resolve(), args.postgres_image)
    elif args.command == 'restore':
        restore(state, args.source.resolve(), args)
    else:
        command = {'build': ('build',), 'up': ('up', '-d', '--wait', '--wait-timeout', '180'), 'status': ('ps',), 'stop': ('stop',), 'logs': ('logs', '--tail', '100')}[args.command]
        if args.command in ('build', 'up', 'stop'):
            with operation_lock(state):
                run(compose(state, *command))
        else:
            run(compose(state, *command))


if __name__ == '__main__':
    signal.signal(signal.SIGTERM, stop_on_signal)
    signal.signal(signal.SIGINT, stop_on_signal)
    try:
        main()
    except (ValueError, RuntimeError, subprocess.CalledProcessError) as exc:
        raise SystemExit(str(exc)) from None
