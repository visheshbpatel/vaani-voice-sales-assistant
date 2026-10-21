import importlib

import pytest

from app import security
from app.config import settings


def test_runtime_call_token_secret_survives_module_reload(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(settings, "APP_SECRET", "")

    importlib.reload(security)
    first_token = security.make_call_token(1)
    importlib.reload(security)

    assert security.valid_call_token(1, first_token)


def test_history_requires_password_when_local_mode_is_disabled(monkeypatch):
    monkeypatch.setattr(settings, "HISTORY_PASSWORD", "")
    monkeypatch.setattr(settings, "ALLOW_LOCAL_RECORDINGS", False)

    with pytest.raises(Exception) as error:
        security.require_history_access(None, None, None, list_access=True)

    assert error.value.status_code == 503
