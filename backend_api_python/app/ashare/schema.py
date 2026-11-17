"""Explicit paper schema deployment, shared by standalone and official migration."""

from contextlib import closing
import os
from pathlib import Path

import psycopg2


MIGRATIONS = ("20261117_ashare_paper.sql", "20261117_ashare_web.sql", "20261117_ashare_operations.sql",
              "20261117_ashare_securities.sql", "20261117_ashare_themes.sql")

def get_database_url():
    """Prefer a dedicated paper database; otherwise use the official PostgreSQL."""
    return (os.getenv("ASHARE_DATABASE_URL") or os.getenv("DATABASE_URL") or "").strip()


def migrate():
    dsn = get_database_url()
    if not dsn:
        raise ValueError("ASHARE_DATABASE_URL or DATABASE_URL required")
    root = Path(__file__).resolve().parents[2] / "migrations"
    with closing(psycopg2.connect(dsn, connect_timeout=10)) as connection:
        with connection, connection.cursor() as cursor:
            for name in MIGRATIONS:
                cursor.execute((root / name).read_text(encoding="utf-8"))


if __name__ == "__main__":
    migrate()
    print("Paper schema ready; existing accounts preserved.")
