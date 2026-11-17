"""One-shot, fail-fast migrations and existing-compatible admin initialization."""
import os

from app.commands.migrate import main as migrate
from app.services.user_service import get_user_service
from app.utils.db import get_db_connection

if len(os.environ.get('SECRET_KEY', '')) < 32:
    raise SystemExit('A persistent SECRET_KEY with at least 32 characters is required')
if os.environ.get('SKIP_STARTUP_HOOKS') != '1':
    raise SystemExit('Paper deployment requires startup hooks disabled')
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
