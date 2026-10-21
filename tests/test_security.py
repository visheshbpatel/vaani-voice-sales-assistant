import importlib

from app import security
from app.config import settings


def test_runtime_call_token_secret_survives_module_reload(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(settings, "APP_SECRET", "")

    importlib.reload(security)
    first_token = security.make_call_token(1)
    importlib.reload(security)

    assert security.valid_call_token(1, first_token)
