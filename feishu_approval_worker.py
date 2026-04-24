#!/usr/bin/env python3
"""
Periodic worker that scans pending Feishu approval requests and processes them once.
"""
from __future__ import annotations

import argparse
import json
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from feishu_approval import ARTIFACTS_DIR, process_dispatch_file
from pricing.execute_saved_plan import DEFAULT_STORAGE_STATE


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Process pending Feishu approval requests.")
    parser.add_argument("--artifacts-dir", default=str(ARTIFACTS_DIR), help="Artifacts directory to scan.")
    parser.add_argument("--storage-state", default=str(DEFAULT_STORAGE_STATE), help="Playwright storage-state path.")
    parser.add_argument("--max-age-hours", type=int, default=48, help="Skip approval files older than this many hours.")
    parser.add_argument("--limit", type=int, default=20, help="Maximum number of pending approvals to inspect per run.")
    parser.add_argument("--max-ops", type=int, default=20, help="Execution guardrail for operation count.")
    parser.add_argument("--commit", action="store_true", help="Actually execute approved plans.")
    parser.add_argument("--notify", action="store_true", help="Reply in Feishu when status changes.")
    return parser.parse_args()


def load_dispatch(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def is_recent(dispatch: dict[str, Any], *, max_age_hours: int) -> bool:
    generated_at = dispatch.get("generatedAt")
    if not generated_at:
        return False
    created = datetime.fromisoformat(generated_at)
    return created >= datetime.now() - timedelta(hours=max_age_hours)


def iter_pending_dispatches(artifacts_dir: Path, *, max_age_hours: int, limit: int) -> list[Path]:
    files = sorted(artifacts_dir.glob("feishu_approval_request_*.json"), reverse=True)
    pending: list[Path] = []
    for path in files:
        dispatch = load_dispatch(path)
        if dispatch.get("status") != "pending":
            continue
        if not is_recent(dispatch, max_age_hours=max_age_hours):
            continue
        pending.append(path)
        if len(pending) >= limit:
            break
    return pending


def main() -> int:
    args = parse_args()
    artifacts_dir = Path(args.artifacts_dir).expanduser()
    pending_files = iter_pending_dispatches(
        artifacts_dir,
        max_age_hours=args.max_age_hours,
        limit=args.limit,
    )

    results = []
    for path in pending_files:
        dispatch = process_dispatch_file(
            path,
            storage_state=args.storage_state,
            max_ops=args.max_ops,
            commit=args.commit,
            notify=args.notify,
        )
        results.append(
            {
                "dispatchFile": str(path),
                "status": dispatch.get("status"),
                "decision": dispatch.get("decision", {}).get("decision"),
                "executedAt": dispatch.get("executedAt"),
            }
        )

    print(
        json.dumps(
            {
                "generatedAt": datetime.now().isoformat(),
                "pendingInspected": len(pending_files),
                "results": results,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
