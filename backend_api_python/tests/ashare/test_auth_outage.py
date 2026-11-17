"""Database outages must not masquerade as expired JWT sessions."""
from contextlib import contextmanager

from flask import Flask, g
import pytest

from app.utils.auth import generate_token, login_required


@pytest.fixture
def auth_client(monkeypatch):
    monkeypatch.setenv("SECRET_KEY", "authentication-outage-test-secret-more-than-32")
    state = {"down": False, "session": 1, "user_version": 1, "role": "user", "calls": 0, "user_down": False}
    class Cursor:
        def execute(self, sql, params):
            self.user_query = "FROM qd_users" in sql
            if self.user_query and state["user_down"]:
                raise ConnectionError("private connection details")
        def fetchone(self):
            if self.user_query:
                return {"username":"example", "role":state["role"], "status":"active", "token_version":state["user_version"]}
            return {"token_version":state["session"]}
        def close(self):
            pass
    class Database:
        def cursor(self):
            return Cursor()
    @contextmanager
    def connect():
        if state["down"]:
            raise ConnectionError("private connection details")
        yield Database()
    monkeypatch.setattr("app.utils.db.get_db_connection", connect)
    app = Flask(__name__)
    @app.get("/protected")
    @login_required
    def protected():
        state["calls"] += 1
        return {"user_id":g.user_id, "role":g.user_role}
    return app.test_client(), state


def test_database_outage_recovers_without_replacing_valid_jwt(auth_client):
    client,state=auth_client
    token=generate_token(41,"example")
    headers={"Authorization":"Bearer "+token}
    assert client.get("/protected",headers=headers).status_code == 200
    state["down"]=True
    response=client.get("/protected",headers=headers)
    assert response.status_code == 503
    assert response.json == {"code":0,"msg":"auth_service_unavailable","data":None}
    assert "private" not in response.text and state["calls"] == 1
    state["down"]=False
    assert client.get("/protected",headers=headers).status_code == 200
    assert state["calls"] == 2


def test_user_lookup_outage_also_returns_503(auth_client):
    client,state=auth_client
    state["user_down"]=True
    response=client.get("/protected",headers={"Authorization":"Bearer "+generate_token(41,"example")})
    assert response.status_code == 503
    assert state["calls"] == 0


def test_invalid_and_revoked_jwt_remain_401(auth_client):
    client,state=auth_client
    assert client.get("/protected",headers={"Authorization":"Bearer invalid"}).status_code == 401
    token=generate_token(41,"example")
    state["session"]=2
    assert client.get("/protected",headers={"Authorization":"Bearer "+token}).status_code == 401
    state["session"]=1
    state["user_version"]=2
    assert client.get("/protected",headers={"Authorization":"Bearer "+token}).status_code == 401
    assert state["calls"] == 0
