from __future__ import annotations

from pathlib import Path

from dotenv import dotenv_values

from app.services.settings.env_file import ENV_FILE_PATH, read_env_file, write_env_file


def test_default_settings_file_is_the_repository_root_env() -> None:
    assert Path(ENV_FILE_PATH) == Path(__file__).resolve().parents[2] / ".env"


def test_settings_env_file_preserves_special_character_values(tmp_path: Path) -> None:
    env_file = tmp_path / "backend.env"
    password = 'Abc #12"\\path$!=O\'Brien'
    env_file.write_text(
        '# Existing comment\nADMIN_PASSWORD="Abc #12\\"\\\\path$!=O\'Brien"\n',
        encoding="utf-8",
    )

    values = read_env_file(str(env_file))

    assert values["ADMIN_PASSWORD"] == password
    values["OPENAI_MODEL"] = "example/model"
    assert write_env_file(values, str(env_file)) is True
    assert dotenv_values(env_file)["ADMIN_PASSWORD"] == password
    assert dotenv_values(env_file)["OPENAI_MODEL"] == "example/model"
    assert "# Existing comment" in env_file.read_text(encoding="utf-8")


def test_settings_atomic_write_keeps_private_permissions_and_survives_reload(tmp_path, monkeypatch):
    import os
    import subprocess
    import sys
    target = tmp_path / 'runtime.env'
    assert write_env_file({'CUSTOM_API_KEY': 'local-test-secret', 'CUSTOM_API_MODEL': 'test-model'}, str(target))
    assert target.stat().st_mode & 0o777 == 0o600
    monkeypatch.setenv('QUANTDINGER_ENV_FILE', str(target))
    environment = os.environ.copy()
    environment.pop('CUSTOM_API_KEY', None)
    environment.pop('CUSTOM_API_MODEL', None)
    result = subprocess.run([sys.executable, '-c',
        "import os; from app.services.settings.env_file import read_env_file; assert os.getenv('CUSTOM_API_KEY') == 'local-test-secret'; assert read_env_file()['CUSTOM_API_MODEL'] == 'test-model'"],
        env=environment, capture_output=True, text=True, check=False)
    assert result.returncode == 0, result.stderr


def test_failed_atomic_replace_keeps_previous_settings_and_removes_temporary_file(tmp_path, monkeypatch):
    from app.services.settings import env_file
    target = tmp_path / 'runtime.env'
    target.write_text('# Keep this\nCUSTOM_API_MODEL=before\n')
    def fail(*args):
        raise OSError('simulated filesystem replace failure')
    monkeypatch.setattr(env_file.os, 'replace', fail)
    assert write_env_file({'CUSTOM_API_MODEL': 'after'}, str(target)) is False
    assert target.read_text() == '# Keep this\nCUSTOM_API_MODEL=before\n'
    assert sorted(path.name for path in tmp_path.iterdir()) == ['runtime.env']


def test_settings_writer_rejects_symlink_without_modifying_target(tmp_path):
    target = tmp_path / 'original.env'
    target.write_text('CUSTOM_API_MODEL=before\n')
    link = tmp_path / 'runtime.env'
    link.symlink_to(target)
    assert write_env_file({'CUSTOM_API_MODEL': 'after'}, str(link)) is False
    assert target.read_text() == 'CUSTOM_API_MODEL=before\n'


def test_real_settings_endpoint_writes_configured_private_file_without_model_calls(tmp_path, monkeypatch):
    from flask import Flask
    import app.routes.settings as route
    import app.services.settings.runtime as runtime
    import app.utils.auth as auth
    target = tmp_path / 'runtime.env'
    monkeypatch.setattr(auth, 'verify_token', lambda token: {
        'user_id': 1, '_verified_username': 'test-admin', '_verified_user_role': 'admin'})
    monkeypatch.setattr(route, 'CONFIG_SCHEMA', {'ai': {'items': [
        {'key': 'CUSTOM_API_KEY', 'type': 'password'}, {'key': 'CUSTOM_API_MODEL', 'type': 'text'}]}})
    monkeypatch.setattr(route, 'read_env_file', lambda: read_env_file(str(target)))
    monkeypatch.setattr(route, 'write_env_file', lambda values: write_env_file(values, str(target)))
    monkeypatch.setattr(runtime, 'ENV_FILE_PATH', str(target))
    monkeypatch.setattr(route, 'refresh_runtime_services', lambda: None)
    monkeypatch.setattr(route, 'clear_config_cache', lambda: None)
    monkeypatch.setenv('CUSTOM_API_KEY', '')
    app = Flask(__name__)
    app.register_blueprint(route.settings_blp, url_prefix='/api/settings')
    client = app.test_client()
    response = client.post('/api/settings/save', json={'ai': {'CUSTOM_API_KEY': 'local-test-secret', 'CUSTOM_API_MODEL': 'test-model'}}, headers={'Authorization': 'Bearer fixture'})
    assert response.status_code == 200 and response.json['code'] == 1
    assert read_env_file(str(target))['CUSTOM_API_KEY'] == 'local-test-secret'
    import os
    assert os.environ['CUSTOM_API_KEY'] == 'local-test-secret'
    assert target.stat().st_mode & 0o777 == 0o600
    masked = client.get('/api/settings/values', headers={'Authorization': 'Bearer fixture'})
    assert masked.json['data']['ai']['CUSTOM_API_KEY'] == ''
    assert masked.json['data']['ai']['CUSTOM_API_KEY_configured'] is True
    assert 'local-test-secret' not in response.text + masked.text
