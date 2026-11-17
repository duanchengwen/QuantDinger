"""One-shot, fail-fast migrations and existing-compatible admin initialization."""
import os
from pathlib import Path

from app.commands.migrate import main as migrate
from app.services.user_service import get_user_service
from app.utils.db import get_db_connection

if len(os.environ.get('SECRET_KEY', '')) < 32:
    raise SystemExit('A persistent SECRET_KEY with at least 32 characters is required')
if os.environ.get('SKIP_STARTUP_HOOKS') != '1':
    raise SystemExit('Paper deployment requires startup hooks disabled')
settings_path = Path(os.environ.get('QUANTDINGER_ENV_FILE', '/app/settings/runtime.env'))
if settings_path.is_symlink():
    raise SystemExit('Mutable settings file must not be a symlink')
if not settings_path.exists():
    descriptor = os.open(settings_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    os.close(descriptor)
os.chmod(settings_path, 0o600)
if not os.access(settings_path, os.R_OK | os.W_OK):
    raise SystemExit('Mutable settings file must be readable and writable by the application user')
migrate()
get_user_service().ensure_admin_exists()
with get_db_connection() as db:
    cursor = db.cursor()
    cursor.execute("SELECT COUNT(*) AS n FROM qd_users WHERE role = 'admin' AND status = 'active'")
    row = cursor.fetchone()
    cursor.close()
    if not row or not row['n']:
        raise SystemExit('Administrator initialization failed; API remains stopped')
print('Paper schema and administrator are ready; existing account passwords preserved.')
