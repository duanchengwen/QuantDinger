"""Read-only paper process health CLI; market readiness is reported separately."""
import argparse
from contextlib import closing
import json

import psycopg2

from app.ashare.operations import read_health
from app.ashare.runtime import utc_now
from app.ashare.schema import get_database_url


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--max-age-seconds', type=int, default=120)
    args = parser.parse_args(argv)
    if not 1 <= args.max_age_seconds <= 3600:
        parser.error('max age must be between 1 and 3600 seconds')
    try:
        dsn = get_database_url()
        if not dsn:
            raise ValueError('database not configured')
        with closing(psycopg2.connect(dsn, connect_timeout=5, options='-c statement_timeout=5000')) as connection:
            result = read_health(connection, utc_now(), args.max_age_seconds)
    except (psycopg2.Error, ValueError):
        result = {'healthy': False, 'mode': 'paper', 'reason': 'database_or_schema_unavailable'}
    print(json.dumps(result, ensure_ascii=False))
    return 0 if result['healthy'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
