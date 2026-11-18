"""Version and human approval records for the deterministic paper strategy."""
import hashlib
import json
import re
import uuid
from decimal import Decimal

from psycopg2.extras import Json, RealDictCursor

from app.ashare.broker import supported


CONFIG_FIELDS = frozenset({'mode', 'kind', 'symbol', 'quantity', 'threshold', 'limit_price', 'max_cash'})
VERSION_ID = re.compile(r'[a-f0-9]{32}')
SHA256 = re.compile(r'[a-f0-9]{64}')
REQUEST_KEY = re.compile(r'[A-Za-z0-9_-]{8,100}')
MONEY = re.compile(r'[0-9]{1,9}(?:\.[0-9]{1,2})?')
SELECT_VERSION = """
    SELECT v.id, v.config, v.config_sha256, v.created_at, v.created_by,
           a.decided_at AS approved_at, a.actor_user_id AS approved_by,
           r.decided_at AS revoked_at, r.actor_user_id AS revoked_by
      FROM qd_ashare_strategy_versions v
      LEFT JOIN qd_ashare_strategy_decisions a
        ON (a.tenant_id, a.account_id, a.version_id) = (v.tenant_id, v.account_id, v.id)
       AND a.decision = 'approved'
      LEFT JOIN qd_ashare_strategy_decisions r
        ON (r.tenant_id, r.account_id, r.version_id) = (v.tenant_id, v.account_id, v.id)
       AND r.decision = 'revoked'
     WHERE v.tenant_id = %s AND v.account_id = %s
"""


def _matches(pattern, value):
    return isinstance(value, str) and pattern.fullmatch(value) is not None


def _positive_cash(value):
    if not _matches(MONEY, value) or Decimal(value) <= 0:
        raise ValueError('invalid_strategy_money')
    return Decimal(value)


def normalize_config(config):
    if not isinstance(config, dict) or set(config) != CONFIG_FIELDS:
        raise ValueError('invalid_strategy_config')
    if config['mode'] != 'paper' or config['kind'] != 'buy_once_below':
        raise ValueError('unsupported_paper_strategy')
    if not supported(config['symbol']):
        raise ValueError('unsupported_strategy_symbol')
    quantity = config['quantity']
    if type(quantity) is not int or not 100 <= quantity <= 1_000_000 or quantity % 100:
        raise ValueError('invalid_strategy_quantity')
    threshold, limit_price, max_cash = (_positive_cash(config[key])
                                        for key in ('threshold', 'limit_price', 'max_cash'))
    if quantity * limit_price >= max_cash:
        raise ValueError('strategy_cash_cap_must_cover_order_and_fees')
    return {**config, 'threshold': f'{threshold:.2f}', 'limit_price': f'{limit_price:.2f}',
            'max_cash': f'{max_cash:.2f}'}


def config_hash(config):
    content = json.dumps(config, sort_keys=True, separators=(',', ':'))
    return hashlib.sha256(content.encode('utf-8')).hexdigest()


class StrategyVersions:
    def __init__(self, connection, tenant_id, account_id):
        self.connection = connection
        self.key = (tenant_id, account_id)

    def _lock_settings(self, cursor):
        cursor.execute('SELECT settings FROM qd_ashare_web WHERE tenant_id=%s AND account_id=%s FOR UPDATE', self.key)
        row = cursor.fetchone()
        if row is None:
            raise ValueError('paper_account_required')
        return row['settings']

    def _operator(self, actor):
        if type(actor) is not int or actor <= 0 or self.key[0] != f'user_{actor}':
            raise ValueError('strategy_operator_required')

    @staticmethod
    def _view(row):
        data = dict(row)
        config = data['config']
        if normalize_config(config) != config or config_hash(config) != data['config_sha256']:
            raise ValueError('strategy_version_integrity_error')
        status = 'draft'
        if data['approved_at'] is not None:
            status = 'approved'
        if data['revoked_at'] is not None:
            status = 'revoked'
        return {**data, 'status': status, 'execution_allowed': False}

    def _find(self, cursor, version_id):
        cursor.execute(SELECT_VERSION + ' AND v.id=%s', (*self.key, version_id))
        row = cursor.fetchone()
        if row is None:
            raise ValueError('strategy_version_not_found')
        return self._view(row)

    def list(self, limit):
        if type(limit) is not int or not 1 <= limit <= 50:
            raise ValueError('invalid_strategy_limit')
        with self.connection, self.connection.cursor(cursor_factory=RealDictCursor) as cursor:
            cursor.execute(SELECT_VERSION + ''' ORDER BY (a.decided_at IS NOT NULL AND r.decided_at IS NULL) DESC,
                v.created_at DESC, v.id DESC LIMIT %s''', (*self.key, limit))
            return {'versions': [self._view(row) for row in cursor.fetchall()]}

    def create(self, config, request_key, actor):
        self._operator(actor)
        if not _matches(REQUEST_KEY, request_key):
            raise ValueError('invalid_strategy_request_key')
        config = normalize_config(config)
        digest = config_hash(config)
        with self.connection, self.connection.cursor(cursor_factory=RealDictCursor) as cursor:
            settings = self._lock_settings(cursor)
            cursor.execute(SELECT_VERSION + ' AND v.request_key=%s', (*self.key, request_key))
            existing = cursor.fetchone()
            if existing is not None:
                version = self._view(existing)
                if version['config_sha256'] != digest:
                    raise ValueError('strategy_request_key_conflict')
                return version
            if config['symbol'] not in settings.get('symbols', []):
                raise ValueError('strategy_symbol_not_watched')
            version_id = uuid.uuid4().hex
            cursor.execute("""INSERT INTO qd_ashare_strategy_versions
                (tenant_id,account_id,id,request_key,config,config_sha256,created_by)
                VALUES (%s,%s,%s,%s,%s,%s,%s)""", (*self.key, version_id, request_key, Json(config), digest, actor))
            return self._find(cursor, version_id)

    def decide(self, version_id, digest, decision, confirmed, actor):
        self._operator(actor)
        if confirmed is not True:
            raise ValueError('explicit_human_confirmation_required')
        if not _matches(VERSION_ID, version_id) or not _matches(SHA256, digest):
            raise ValueError('invalid_strategy_version_or_hash')
        if decision not in ('approved', 'revoked'):
            raise ValueError('invalid_strategy_decision')
        with self.connection, self.connection.cursor(cursor_factory=RealDictCursor) as cursor:
            settings = self._lock_settings(cursor)
            version = self._find(cursor, version_id)
            if version['config_sha256'] != digest:
                raise ValueError('strategy_version_hash_mismatch')
            if version['status'] == decision:
                return version
            if (version['status'], decision) not in (('draft', 'approved'), ('approved', 'revoked')):
                raise ValueError('invalid_strategy_state_transition')
            if decision == 'approved':
                if version['config']['symbol'] not in settings.get('symbols', []):
                    raise ValueError('strategy_symbol_not_watched')
                cursor.execute(SELECT_VERSION + ' AND a.decided_at IS NOT NULL AND r.decided_at IS NULL LIMIT 1', self.key)
                if cursor.fetchone() is not None:
                    raise ValueError('another_strategy_version_approved')
            cursor.execute("""INSERT INTO qd_ashare_strategy_decisions
                (tenant_id,account_id,version_id,config_sha256,decision,actor_user_id)
                VALUES (%s,%s,%s,%s,%s,%s)""", (*self.key, version_id, digest, decision, actor))
            return self._find(cursor, version_id)
