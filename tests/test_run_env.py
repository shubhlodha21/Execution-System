import importlib.util
import os
from pathlib import Path

from execsys.config import load_settings


ROOT = Path(__file__).resolve().parents[1]


def _run_module():
    spec = importlib.util.spec_from_file_location("run_entry", ROOT / "run.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_dotenv_strips_inline_comments_and_quotes(tmp_path, monkeypatch):
    for key in ("IB_ACCOUNT_ID", "RUN_MODE", "IB_PORT", "X_QUOTED"):
        monkeypatch.delenv(key, raising=False)
    env = tmp_path / ".env"
    env.write_text(
        "# comment\n"
        "IB_ACCOUNT_ID=DU7654321           # your account\n"
        "RUN_MODE=data-only\t# signals only\n"
        "IB_PORT=4002\n"
        'X_QUOTED="a # not a comment"  # real comment\n',
    )
    _run_module().load_dotenv(env)
    assert os.environ["IB_ACCOUNT_ID"] == "DU7654321"
    assert os.environ["RUN_MODE"] == "data-only"
    assert os.environ["IB_PORT"] == "4002"
    assert os.environ["X_QUOTED"] == "a # not a comment"


def test_example_env_drives_settings():
    values = {}
    for line in (ROOT / ".env.example").read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, value = line.split("=", 1)
            values[key] = value.split("#", 1)[0].strip()
    s = load_settings(ROOT / "config/settings.yaml", env=values)
    assert s.ib.client_id == 99 and s.ib.port == 4002
    assert s.ib.market_data_type == "realtime"
    assert s.telemetry.enabled is False
    assert values["RUN_MODE"] == "paper"
