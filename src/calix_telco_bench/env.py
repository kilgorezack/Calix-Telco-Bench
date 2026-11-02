"""Local, untracked runtime configuration for evaluator integrations."""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any


def default_env_file(config_path: Path) -> Path:
    """Locate the untracked `.env` that belongs to an evaluation config.

    ``setup.sh`` and the README place `.env` at the repository root, while
    configs live in ``config/`` or ``config/local/``. Search from the config's
    directory upward and stop at the checkout root (the first directory holding
    ``pyproject.toml`` or ``.git``). If none exists, return the checkout-root
    location so error messages name the documented path.
    """
    start = config_path.resolve().parent
    checkout_root: Path | None = None
    for directory in (start, *start.parents):
        candidate = directory / ".env"
        if candidate.is_file():
            return candidate
        if (directory / "pyproject.toml").is_file() or (directory / ".git").exists():
            checkout_root = directory
            break
    return (checkout_root or start) / ".env"


def load_runtime_env(env_file: Path | None = None) -> Path | None:
    """Load an untracked `.env` without overriding exported variables."""
    try:
        from dotenv import load_dotenv
    except ImportError as exc:
        raise RuntimeError("install calix-telco-bench to load .env configuration") from exc
    resolved = (env_file or Path.cwd() / ".env").resolve()
    if resolved.is_file():
        load_dotenv(resolved, override=False)
        return resolved
    return None


def load_judge_env(env_file: Path | None = None) -> dict[str, str | None]:
    """Load judge metadata from `.env` without overriding exported variables.

    The utility intentionally returns identity metadata only. Provider API keys
    remain in the process environment and are never copied to evaluation
    provenance, YAML configuration, logs, or the terminal monitor.
    """
    resolved = load_runtime_env(env_file)
    return {
        "provider": os.getenv("CALIX_TELCO_BENCH_JUDGE_PROVIDER") or None,
        "model": os.getenv("CALIX_TELCO_BENCH_JUDGE_MODEL") or None,
        "model_revision": os.getenv("CALIX_TELCO_BENCH_JUDGE_MODEL_REVISION") or None,
        "env_file": str(resolved) if resolved is not None else None,
    }
