#!/usr/bin/env python3
"""
Runtime configuration helpers for travel automation.
"""
from __future__ import annotations

import os
import shlex
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent
ENV_FILE = PROJECT_ROOT / "travel_automation.env"
LOCAL_ENV_FILE = PROJECT_ROOT / "travel_automation.local.env"


def _parse_env_line(raw_line: str) -> tuple[str, str] | None:
    line = raw_line.strip()
    if not line or line.startswith("#"):
        return None
    if line.startswith("export "):
        line = line[len("export ") :].strip()
    if "=" not in line:
        return None

    key, value = line.split("=", 1)
    key = key.strip()
    if not key:
        return None

    try:
        tokens = shlex.split(value, comments=True, posix=True)
    except ValueError:
        tokens = [value.strip()]

    if not tokens:
        return key, ""
    return key, " ".join(tokens)


def load_env_file(path: Path) -> None:
    if not path.exists():
        return

    for raw_line in path.read_text(encoding="utf-8").splitlines():
        parsed = _parse_env_line(raw_line)
        if parsed is None:
            continue
        key, value = parsed
        os.environ[key] = value


def load_project_env() -> None:
    load_env_file(ENV_FILE)
    load_env_file(LOCAL_ENV_FILE)


def require_env(name: str) -> str:
    value = os.environ.get(name)
    if value:
        return value
    raise RuntimeError(f"missing required environment variable: {name}")
