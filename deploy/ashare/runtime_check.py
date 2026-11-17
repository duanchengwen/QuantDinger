"""Small process checks; no credentials or account details in output."""
import os
import sys
import urllib.error
import urllib.request

import psycopg2


def main():
    try:
        if len(os.environ.get('SECRET_KEY', '')) < 32:
            raise ValueError('persistent_secret_required')
        if os.environ.get('SKIP_STARTUP_HOOKS') != '1':
            raise ValueError('background_hooks_must_be_disabled')
        if sys.argv[1:] == ['api']:
            with urllib.request.urlopen('http://127.0.0.1:5000/health', timeout=5) as response:
                if response.status != 200:
                    return 1
        with psycopg2.connect(os.environ['DATABASE_URL'], connect_timeout=5) as connection, connection.cursor() as cursor:
            cursor.execute('SELECT 1')
            if cursor.fetchone() != (1,):
                return 1
        return 0
    except (ValueError, KeyError, OSError, psycopg2.Error) as exc:
        print('paper_runtime_unavailable:' + type(exc).__name__)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
