"""Regression tests for credential transport, deletion scope, and PID safety."""
import json
import os
import signal
import ssl
import subprocess
import sys
from pathlib import Path

import pytest

from calix_telco_bench import control
from calix_telco_bench.env import default_env_file
from calix_telco_bench.judging import JUDGE_CA_BUNDLE_ENV, JudgeError, _judge_tls_verify, judge_api_key
from calix_telco_bench.plan import _target_output

ROOT = Path(__file__).parents[1]


# --- Judge TLS verification -------------------------------------------------

def test_judge_tls_verification_is_on_by_default(monkeypatch) -> None:
    monkeypatch.delenv(JUDGE_CA_BUNDLE_ENV, raising=False)
    assert _judge_tls_verify() is True


def test_judge_tls_rejects_missing_ca_bundle(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv(JUDGE_CA_BUNDLE_ENV, str(tmp_path / "missing.pem"))
    with pytest.raises(JudgeError, match=JUDGE_CA_BUNDLE_ENV):
        _judge_tls_verify()


def test_judge_tls_uses_private_ca_bundle(monkeypatch) -> None:
    cafile = ssl.get_default_verify_paths().cafile
    if not cafile or not Path(cafile).is_file():
        pytest.skip("no system CA bundle available")
    monkeypatch.setenv(JUDGE_CA_BUNDLE_ENV, cafile)
    context = _judge_tls_verify()
    assert isinstance(context, ssl.SSLContext)
    assert context.verify_mode == ssl.CERT_REQUIRED
    assert context.check_hostname is True


@pytest.mark.parametrize("path", ["src/calix_telco_bench", "scripts"])
def test_no_judge_transport_disables_verification(path: str) -> None:
    for source in (ROOT / path).rglob("*.py"):
        assert "verify=False" not in source.read_text(encoding="utf-8"), source


# --- Credentials ------------------------------------------------------------

def test_placeholder_auth_token_does_not_shadow_api_key(monkeypatch) -> None:
    monkeypatch.setenv("ANTHROPIC_AUTH_TOKEN", "replace-with-your-token")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "real-key")
    assert judge_api_key() == "real-key"


def test_placeholder_only_credential_is_unset(monkeypatch) -> None:
    monkeypatch.setenv("ANTHROPIC_AUTH_TOKEN", "replace-with-your-token")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "  ")
    assert judge_api_key() is None


def test_env_example_ships_no_active_credentials() -> None:
    for line in (ROOT / ".env.example").read_text(encoding="utf-8").splitlines():
        if line.strip().startswith("#") or "=" not in line:
            continue
        name = line.split("=", 1)[0].strip()
        assert not name.endswith(("_TOKEN", "_KEY")), line


# --- Evidence deletion scope ------------------------------------------------

@pytest.mark.parametrize("value", ["", "  ", None, "/", "/etc", "..", "../sibling", "a/../../b", "."])
def test_target_output_rejects_paths_outside_output_root(tmp_path: Path, value) -> None:
    with pytest.raises(ValueError):
        _target_output(tmp_path / "root", value, "t")


def test_target_output_rejects_symlink_escape(tmp_path: Path) -> None:
    root = tmp_path / "root"; root.mkdir()
    (root / "link").symlink_to(tmp_path / "outside", target_is_directory=True)
    with pytest.raises(ValueError):
        _target_output(root, "link", "t")


def test_target_output_accepts_nested_relative_path(tmp_path: Path) -> None:
    root = tmp_path / "root"
    assert _target_output(root, "routed-teachers/edge", "t") == (root / "routed-teachers" / "edge").resolve()


# --- PID identity before signalling -----------------------------------------

def test_managed_status_requires_recorded_start_time() -> None:
    running, reason = control._managed_status({"pid": os.getpid(), "command": [sys.executable]})
    assert running is False
    assert "start time" in reason


def _stop_pipeline_with_state(tmp_path: Path, monkeypatch, active_judges: dict) -> dict:
    (tmp_path / "pipeline-state.json").write_text(json.dumps({"active_judges": active_judges}), encoding="utf-8")
    monkeypatch.setattr(control, "pipeline_status", lambda *_: {"output": str(tmp_path), "pid": None, "running": False})
    monkeypatch.setattr(control, "_judge_all_root", lambda *_: tmp_path)
    monkeypatch.setattr(control, "stop", lambda *_: {"stopped": False})
    monkeypatch.setattr(control, "stop_judge_all", lambda *_: {"stopped": False})
    return control.stop_pipeline({"targets": {}}, tmp_path / "config.yaml")


def test_stop_pipeline_does_not_signal_unverified_judge_pids(tmp_path: Path, monkeypatch) -> None:
    signalled: list[int] = []
    monkeypatch.setattr(control.os, "killpg", lambda pid, _sig: signalled.append(pid))
    result = _stop_pipeline_with_state(tmp_path, monkeypatch, {
        "legacy:final": os.getpid(),
        "reused:final": {"pid": os.getpid(), "process_start_ticks": "0", "command": [sys.executable]},
    })
    assert signalled == []
    assert result["stopped"] is False
    assert result["pipeline_judges"]["legacy:final"]["stopped"] is False
    assert result["pipeline_judges"]["reused:final"]["reason"] == "PID was reused"


@pytest.mark.skipif(not Path("/proc/self/stat").exists(), reason="requires Linux /proc")
def test_stop_pipeline_signals_verified_judge(tmp_path: Path, monkeypatch) -> None:
    command = [sys.executable, "-c", "import time; time.sleep(60)"]
    process = subprocess.Popen(command, start_new_session=True)
    try:
        record = {"pid": process.pid, "process_start_ticks": control._process_start_ticks(process.pid), "command": command}
        result = _stop_pipeline_with_state(tmp_path, monkeypatch, {"t:final": record})
        assert result["pipeline_judges"]["t:final"] == {"stopped": True, "signal": "SIGTERM"}
        assert process.wait(timeout=10) == -signal.SIGTERM
    finally:
        if process.poll() is None:
            process.kill()


# --- .env discovery ---------------------------------------------------------

def test_default_env_file_finds_repository_root_env(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text("", encoding="utf-8")
    (tmp_path / ".env").write_text("", encoding="utf-8")
    config = tmp_path / "config" / "local" / "run.yaml"
    config.parent.mkdir(parents=True)
    assert default_env_file(config) == (tmp_path / ".env").resolve()


def test_default_env_file_prefers_nearest_env(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text("", encoding="utf-8")
    (tmp_path / ".env").write_text("", encoding="utf-8")
    (tmp_path / "config").mkdir()
    (tmp_path / "config" / ".env").write_text("", encoding="utf-8")
    assert default_env_file(tmp_path / "config" / "default.yaml") == (tmp_path / "config" / ".env").resolve()


def test_default_env_file_does_not_search_above_checkout(tmp_path: Path) -> None:
    (tmp_path / ".env").write_text("", encoding="utf-8")  # outside the checkout
    checkout = tmp_path / "checkout"
    (checkout / "config").mkdir(parents=True)
    (checkout / "pyproject.toml").write_text("", encoding="utf-8")
    assert default_env_file(checkout / "config" / "default.yaml") == (checkout / ".env").resolve()
