"""Version and human approval records for the deterministic paper strategy."""
import hashlib
import json
import re
import uuid
from decimal import Decimal

from psycopg2.extras import Json, RealDictCursor

from app.ashare.broker import PaperBroker, SHANGHAI, STRATEGY_ORDER_PREFIX, aware, supported
from app.ashare.calendar_rules import market_session
from app.ashare.market import SOURCE, validate_quote
from app.ashare.runtime import utc_now


CONFIG_FIELDS = frozenset({'mode', 'kind', 'symbol', 'quantity', 'threshold', 'limit_price', 'max_cash'})
RESEARCH_REFERENCE = 'research_reference'
VERSION_ID = re.compile(r'[a-f0-9]{32}')
SHA256 = re.compile(r'[a-f0-9]{64}')
REQUEST_KEY = re.compile(r'[A-Za-z0-9_-]{8,100}')
MONEY = re.compile(r'[0-9]{1,9}(?:\.[0-9]{1,2})?')
SELECT_VERSION = f"""
    SELECT v.id, v.config, v.config_sha256, v.created_at, v.created_by,
           a.decided_at AS approved_at, a.actor_user_id AS approved_by,
           r.decided_at AS revoked_at, r.actor_user_id AS revoked_by,
           x.activated_at, x.actor_user_id AS activated_by,
           CASE WHEN o.id IS NOT NULL THEN jsonb_build_object(
               'id', o.id::text, 'status', o.status, 'submitted_at', o.submitted_at,
               'filled', o.filled, 'quantity', o.quantity,
               'filled_amount', o.notional::text, 'fees', (o.commission + o.transfer_fee + o.sell_tax)::text,
               'reserved_cash', o.reserved_cash::text
           ) END AS "order"
      FROM qd_ashare_strategy_versions v
      LEFT JOIN qd_ashare_strategy_decisions a
        ON (a.tenant_id, a.account_id, a.version_id) = (v.tenant_id, v.account_id, v.id)
       AND a.decision = 'approved'
      LEFT JOIN qd_ashare_strategy_decisions r
        ON (r.tenant_id, r.account_id, r.version_id) = (v.tenant_id, v.account_id, v.id)
       AND r.decision = 'revoked'
      LEFT JOIN qd_ashare_strategy_activations x
        ON (x.tenant_id, x.account_id, x.version_id, x.config_sha256)
         = (v.tenant_id, v.account_id, v.id, v.config_sha256)
      LEFT JOIN qd_ashare_orders o
        ON x.activated_at IS NOT NULL AND (o.tenant_id, o.account_id, o.client_order_id)
         = (v.tenant_id, v.account_id, '{STRATEGY_ORDER_PREFIX}' || v.id)
     WHERE v.tenant_id = %s AND v.account_id = %s
"""


def _matches(pattern, value):
    return isinstance(value, str) and pattern.fullmatch(value) is not None


def _positive_cash(value):
    if not _matches(MONEY, value) or Decimal(value) <= 0:
        raise ValueError('invalid_strategy_money')
    return Decimal(value)


def normalize_config(config):
    if not isinstance(config, dict) or set(config) - {RESEARCH_REFERENCE} != CONFIG_FIELDS:
        raise ValueError('invalid_strategy_config')
    if RESEARCH_REFERENCE in config:
        reference = config[RESEARCH_REFERENCE]
        if (not isinstance(reference, dict) or set(reference) != {'run_id', 'input_hash', 'output_hash'}
                or not _matches(VERSION_ID, reference['run_id'])
                or not all(_matches(SHA256, reference[key]) for key in ('input_hash', 'output_hash'))):
            raise ValueError('invalid_strategy_research_reference')
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
    def __init__(self, connection, tenant_id, account_id, *, clock=utc_now):
        self.connection = connection
        self.key = (tenant_id, account_id)
        self.broker = PaperBroker(connection, *self.key)
        self.clock = clock

    def _lock_account(self, cursor):
        # Match, submit and revoke all start with the ledger row, before the web row.
        try:
            account = self.broker._account(cursor)
        except ValueError:
            raise ValueError('paper_account_required') from None
        cursor.execute('SELECT settings,rules FROM qd_ashare_web WHERE tenant_id=%s AND account_id=%s FOR UPDATE', self.key)
        row = cursor.fetchone()
        if row is None:
            raise ValueError('paper_account_required')
        return account, row

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
        return {**data, 'status': status,
                'execution_allowed': status == 'approved' and data['activated_at'] is not None}

    def _find(self, cursor, version_id):
        cursor.execute(SELECT_VERSION + ' AND v.id=%s', (*self.key, version_id))
        row = cursor.fetchone()
        if row is None:
            raise ValueError('strategy_version_not_found')
        return self._view(row)

    def _verify_research(self, cursor, config):
        reference = config.get(RESEARCH_REFERENCE)
        if reference is None:
            return
        from app.ashare.research import ResearchError, ResearchStore

        try:
            run = ResearchStore(self.connection, *self.key).find_run(cursor, reference['run_id'])
        except ResearchError:
            raise ValueError('strategy_research_unavailable') from None
        if (run is None or run['status'] != 'completed'
                or any(run[key] != reference[key] for key in ('input_hash', 'output_hash'))
                or not any(candidate['symbol'] == config['symbol'] for candidate in run['candidates'])):
            raise ValueError('strategy_research_unavailable')

    def _funding(self, config, account):
        required = self.broker._buy_reserve(config['quantity'], Decimal(config['limit_price']))
        available = account['cash'] - account['reserved_cash']
        return {'required_cash': required, 'available_cash': available,
                'cap_sufficient': required <= Decimal(config['max_cash']), 'cash_sufficient': required <= available}

    def _validate_cap(self, config, account):
        funding = self._funding(config, account)
        if not funding['cap_sufficient']:
            raise ValueError('strategy_cash_cap_exceeded')
        return funding

    def list(self, limit):
        if type(limit) is not int or not 1 <= limit <= 50:
            raise ValueError('invalid_strategy_limit')
        with self.connection, self.connection.cursor(cursor_factory=RealDictCursor) as cursor:
            cursor.execute(SELECT_VERSION + ''' ORDER BY (a.decided_at IS NOT NULL AND r.decided_at IS NULL) DESC,
                v.created_at DESC, v.id DESC LIMIT %s''', (*self.key, limit))
            versions = [self._view(row) for row in cursor.fetchall()]
            if versions:
                account = self.broker._account(cursor, lock=False)
                for version in versions:
                    version['funding'] = self._funding(version['config'], account)
            return {'versions': versions}

    def get(self, version_id):
        if not _matches(VERSION_ID, version_id):
            raise ValueError('invalid_strategy_version_or_hash')
        with self.connection, self.connection.cursor(cursor_factory=RealDictCursor) as cursor:
            version = self._find(cursor, version_id)
            account = self.broker._account(cursor, lock=False)
            return {**version, 'funding': self._funding(version['config'], account)}

    def claim_review_notice(self, version_id):
        with self.connection, self.connection.cursor(cursor_factory=RealDictCursor) as cursor:
            self._lock_account(cursor)
            if self._find(cursor, version_id)['status'] != 'draft':
                return False
            cursor.execute("""SELECT 1 FROM qd_ashare_audit
                WHERE tenant_id=%s AND account_id=%s AND event='strategy_review_notice_intent'
                  AND details->>'version_id'=%s LIMIT 1""", (*self.key, version_id))
            if cursor.fetchone() is not None:
                return False
            self.broker._audit(cursor, 'strategy_review_notice_intent', {'version_id': version_id})
            return True

    def record_review_notice(self, version_id, status):
        with self.connection, self.connection.cursor() as cursor:
            self.broker._audit(cursor, 'strategy_review_notice_result', {'version_id': version_id, 'status': status})

    def create(self, config, request_key, actor):
        self._operator(actor)
        if not _matches(REQUEST_KEY, request_key):
            raise ValueError('invalid_strategy_request_key')
        config = normalize_config(config)
        digest = config_hash(config)
        with self.connection, self.connection.cursor(cursor_factory=RealDictCursor) as cursor:
            account, web = self._lock_account(cursor)
            cursor.execute(SELECT_VERSION + ' AND v.request_key=%s', (*self.key, request_key))
            existing = cursor.fetchone()
            if existing is not None:
                version = self._view(existing)
                if version['config_sha256'] != digest:
                    raise ValueError('strategy_request_key_conflict')
                version['funding'] = self._funding(config, account)
                return version
            if config['symbol'] not in web['settings'].get('symbols', []):
                raise ValueError('strategy_symbol_not_watched')
            funding = self._validate_cap(config, account)
            self._verify_research(cursor, config)
            version_id = uuid.uuid4().hex
            cursor.execute("""INSERT INTO qd_ashare_strategy_versions
                (tenant_id,account_id,id,request_key,config,config_sha256,created_by)
                VALUES (%s,%s,%s,%s,%s,%s,%s)""", (*self.key, version_id, request_key, Json(config), digest, actor))
            return {**self._find(cursor, version_id), 'funding': funding}

    def decide(self, version_id, digest, decision, confirmed, actor):
        self._operator(actor)
        if confirmed is not True:
            raise ValueError('explicit_human_confirmation_required')
        if not _matches(VERSION_ID, version_id) or not _matches(SHA256, digest):
            raise ValueError('invalid_strategy_version_or_hash')
        if decision not in ('approved', 'activated', 'revoked'):
            raise ValueError('invalid_strategy_decision')
        with self.connection, self.connection.cursor(cursor_factory=RealDictCursor) as cursor:
            account, web = self._lock_account(cursor)
            version = self._find(cursor, version_id)
            if version['config_sha256'] != digest:
                raise ValueError('strategy_version_hash_mismatch')
            if decision == 'activated':
                if version['status'] != 'approved':
                    raise ValueError('invalid_strategy_state_transition')
                if version['execution_allowed']:
                    return version
                if version['config']['symbol'] not in web['settings'].get('symbols', []):
                    raise ValueError('strategy_symbol_not_watched')
                self._validate_cap(version['config'], account)
                self._verify_research(cursor, version['config'])
                cursor.execute("""INSERT INTO qd_ashare_strategy_activations
                    (tenant_id,account_id,version_id,config_sha256,actor_user_id,activated_at)
                    VALUES (%s,%s,%s,%s,%s,%s)""", (*self.key, version_id, digest, actor, self.clock()))
                return self._find(cursor, version_id)
            if version['status'] == decision:
                return version
            if (version['status'], decision) not in (('draft', 'approved'), ('approved', 'revoked')):
                raise ValueError('invalid_strategy_state_transition')
            if decision == 'approved':
                if version['config']['symbol'] not in web['settings'].get('symbols', []):
                    raise ValueError('strategy_symbol_not_watched')
                self._validate_cap(version['config'], account)
                self._verify_research(cursor, version['config'])
                cursor.execute(SELECT_VERSION + ' AND a.decided_at IS NOT NULL AND r.decided_at IS NULL LIMIT 1', self.key)
                if cursor.fetchone() is not None:
                    raise ValueError('another_strategy_version_approved')
            cursor.execute("""INSERT INTO qd_ashare_strategy_decisions
                (tenant_id,account_id,version_id,config_sha256,decision,actor_user_id)
                VALUES (%s,%s,%s,%s,%s,%s)""", (*self.key, version_id, digest, decision, actor))
            if decision == 'revoked' and version['execution_allowed']:
                order = self.broker._find_order(cursor, STRATEGY_ORDER_PREFIX + version_id)
                if order and order['status'] in ('open', 'partial'):
                    self.broker._release(cursor, order, 'cancelled')
            return self._find(cursor, version_id)

    def submit_on_quote(self, quote, reviewed):
        """Submit once for an activated version and a newly accepted, human-reviewed quote."""
        if (reviewed.get('approval_method') != 'explicit_human_review'
                or quote.get('source') != SOURCE or not _matches(SHA256, quote.get('evidence_sha256'))):
            return None
        symbol = quote['symbol']
        with self.connection, self.connection.cursor(cursor_factory=RealDictCursor) as cursor:
            account, web = self._lock_account(cursor)
            if symbol not in web['settings'].get('symbols', []) or web['rules'].get(symbol) != reviewed:
                return None
            cursor.execute(SELECT_VERSION + """ AND a.decided_at IS NOT NULL AND r.decided_at IS NULL
                AND x.activated_at IS NOT NULL AND v.config->>'symbol'=%s LIMIT 2""", (*self.key, symbol))
            rows = cursor.fetchall()
            if len(rows) != 1:
                return None
            version = self._view(rows[0])
            if reviewed.get('reviewed_by') != version['activated_by']:
                return None
            config = version['config']
            now = self.clock()
            validate_quote(quote, now=now)
            exchange_time = aware(quote['exchange_time'])
            if (not market_session(now)['can_match'] or not market_session(exchange_time)['can_match']
                    or exchange_time <= max(version['activated_at'], aware(reviewed['reviewed_at']))):
                return None
            if Decimal(quote['last']) > Decimal(config['threshold']):
                return None
            price = Decimal(config['limit_price'])
            if not Decimal(reviewed['limit_down']) <= price <= Decimal(reviewed['limit_up']):
                raise ValueError('strategy_price_outside_reviewed_band')
            cursor.execute("""SELECT quote_id FROM qd_ashare_quotes WHERE tenant_id=%s AND account_id=%s
                AND symbol=%s ORDER BY exchange_time DESC LIMIT 1""", (*self.key, symbol))
            accepted = cursor.fetchone()
            if accepted is None or accepted['quote_id'] != quote['quote_id']:
                return None
            key = STRATEGY_ORDER_PREFIX + version['id']
            if self.broker._find_order(cursor, key) is not None:
                return None
            self._verify_research(cursor, config)
            order = self.broker._place_order_locked(
                cursor, account, key, symbol, 'buy', config['quantity'], price, now,
                now.astimezone(SHANGHAI).date(), max_cash=Decimal(config['max_cash']),
            )
            self.broker._audit(cursor, 'approved_strategy_order_placed', {
                'order_id': order['id'], 'version_id': version['id'], 'config_sha256': version['config_sha256'],
                'activated_by': version['activated_by'], 'activated_at': version['activated_at'],
                'quote_id': quote['quote_id'], 'exchange_time': exchange_time, 'evidence_sha256': quote['evidence_sha256'],
            })
            return order
