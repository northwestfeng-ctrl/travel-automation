#!/usr/bin/env python3
"""
Shared path helpers for pricing and ebooking utilities.
"""
from __future__ import annotations

import os
from datetime import datetime
from pathlib import Path


PRICING_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = PRICING_DIR.parent
ARTIFACTS_DIR = PRICING_DIR / "artifacts"


def default_storage_state_path() -> Path:
    configured = os.environ.get("CTRIP_STORAGE_STATE")
    if configured:
        return Path(configured).expanduser()

    candidates = [
        Path.home() / ".openclaw" / "credentials" / "ctrip-ebooking-auth.json",
        Path.home() / ".credentials" / "ctrip-ebooking-auth.json",
    ]
    for candidate in candidates:
        if candidate.exists():
            return candidate
    return candidates[0]


DEFAULT_STORAGE_STATE_PATH = default_storage_state_path()


def captured_requests_path(tool_name: str) -> Path:
    configured = os.environ.get("CTRIP_CAPTURED_REQUESTS_FILE")
    if configured:
        return Path(configured).expanduser()

    ARTIFACTS_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    safe_tool_name = "".join(ch if ch.isalnum() or ch in {"_", "-"} else "_" for ch in tool_name)
    return ARTIFACTS_DIR / f"{safe_tool_name}_captured_requests_{stamp}.json"
