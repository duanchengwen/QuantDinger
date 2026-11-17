"""Self-hosted password changes must work without an email transport."""
from contextlib import contextmanager
from unittest.mock import Mock

import pytest

from app.services import user_preferences as preferences
from app.services.email_service import EmailService
from app.services.user_service import UserService
from app.services.security_service import SecurityService


@pytest.fixture
def password_service(monkeypatch):
    crypto = UserService()
    state = {'hash': crypto.hash_password('CurrentPassword123!')}
    security = Mock()
    security.validate_password_strength.side_effect = SecurityService().validate_password_strength
    security.check_login_allowed.return_value = (True, '')
    monkeypatch.setattr(preferences, 'get_security_service', lambda: security)
    users = Mock()
    users.security = security
    users.get_user_by_id.return_value = {'id': 73, 'username': 'local-user'}
    users.change_password.side_effect = lambda uid, old, new: crypto.verify_password(old, state['hash'])
    users.reset_password.return_value = True
    monkeypatch.setattr(preferences, 'get_user_service', lambda: users)
    class Cursor:
        def execute(self, sql, params):
            assert params == (73,)
        def fetchone(self):
            return {'password_hash': state['hash']}
        def close(self):
            pass
    class Database:
        def cursor(self):
            return Cursor()
    @contextmanager
    def connect():
        yield Database()
    monkeypatch.setattr(preferences, 'get_db_connection', connect)
    return users, state


def test_current_password_works_without_email(password_service):
    users, _ = password_service
    assert preferences.change_user_password(73, 'CurrentPassword123!', 'NewPassword123!') == (True, 'Password changed successfully', 200)
    users.change_password.assert_called_once_with(73, 'CurrentPassword123!', 'NewPassword123!')


@pytest.mark.parametrize('old', ['', 'wrong-password'])
def test_wrong_or_missing_current_password_cannot_change_account(password_service, old):
    users, _ = password_service
    result = preferences.change_user_password(73, old, 'NewPassword123!')
    assert result[0] is False and result[2] == 400
    users.reset_password.assert_not_called()


def test_passwordless_account_requires_email_verification(password_service):
    users, state = password_service
    state['hash'] = ''
    result = preferences.change_user_password(73, 'anything', 'NewPassword123!')
    assert result == (False, 'Email verification required to set initial password', 400)
    users.reset_password.assert_not_called()


@pytest.mark.parametrize('password', ['short1A', 'lowercase123', 'UPPERCASE123', 'NoDigitsHere'])
def test_current_password_route_uses_same_strength_policy(password_service, password):
    users, _ = password_service
    assert preferences.change_user_password(73, 'CurrentPassword123!', password)[0] is False
    users.change_password.assert_not_called()


def test_unconfigured_email_never_creates_an_undeliverable_code(monkeypatch):
    monkeypatch.delenv('SMTP_HOST', raising=False)
    email = EmailService()
    email.create_verification_code = Mock(return_value=(True, '123456'))
    email.send_email = Mock(return_value=(False, 'Email service is not configured'))
    assert email.send_verification_code('test@example.com', 'change_password') == (False, 'Email service is not configured')
    email.create_verification_code.assert_not_called()


def test_repeated_wrong_password_is_rate_limited(password_service):
    users, _ = password_service
    users.security.check_login_allowed.return_value = (False, 'blocked')
    assert preferences.change_user_password(73, 'wrong', 'NewPassword123!')[2] == 429
    users.change_password.assert_not_called()


def test_incorrect_password_records_attempt(password_service):
    users, _ = password_service
    preferences.change_user_password(73, 'wrong', 'NewPassword123!', ip_address='127.0.0.1')
    users.security.record_login_attempt.assert_called_once_with('password-change:73', 'account', False, '127.0.0.1', '')


def test_http_current_password_route_uses_authenticated_user(password_service, monkeypatch):
    from flask import Flask
    from app.routes.user import user_blp
    monkeypatch.setattr('app.utils.auth.verify_token', lambda _: {
        'user_id': 73, '_verified_username': 'local-user', '_verified_user_role': 'user'
    })
    app = Flask(__name__)
    app.register_blueprint(user_blp, url_prefix='/api/users')
    client = app.test_client()
    body = {'old_password': 'CurrentPassword123!', 'new_password': 'NewPassword123!', 'user_id': 999}
    assert client.post('/api/users/change-password', json=body).status_code == 401
    response = client.post('/api/users/change-password', json=body, headers={'Authorization': 'Bearer fixture'})
    assert response.status_code == 200 and response.json['code'] == 1
    password_service[0].change_password.assert_called_once_with(73, 'CurrentPassword123!', 'NewPassword123!')


def test_http_current_password_route_preserves_failure(password_service, monkeypatch):
    from flask import Flask
    from app.routes.user import user_blp
    monkeypatch.setattr('app.utils.auth.verify_token', lambda _: {
        'user_id': 73, '_verified_username': 'local-user', '_verified_user_role': 'user'
    })
    app = Flask(__name__)
    app.register_blueprint(user_blp, url_prefix='/api/users')
    response = app.test_client().post('/api/users/change-password',
        json={'old_password': 'wrong', 'new_password': 'NewPassword123!'},
        headers={'Authorization': 'Bearer fixture'})
    assert response.status_code == 400
    assert response.json == {'code': 0, 'msg': 'Old password incorrect', 'data': None}
    password_service[0].reset_password.assert_not_called()
