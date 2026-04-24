#!/usr/bin/env python3
"""
Shared path helpers for competitor scraping scripts.
"""
from __future__ import annotations

import os
from pathlib import Path


COMPETITOR_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = COMPETITOR_DIR.parent
DEFAULT_RESULTS_DIR = COMPETITOR_DIR / "results"


def competitor_results_dir() -> Path:
    configured = os.environ.get("COMPETITOR_RESULTS_DIR")
    if configured:
        return Path(configured).expanduser()
    return DEFAULT_RESULTS_DIR
