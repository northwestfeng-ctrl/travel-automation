#!/usr/bin/env python3
"""
Execute a previously generated ebooking execution plan with safety guards.

Default mode is dry-run. Use --commit explicitly to submit the saved plan.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from copy import deepcopy
from datetime import datetime
from pathlib import Path
from typing import Any

from ebooking_batch_price_api import EBookingBatchPriceClient
from recommendation_to_execution_plan import (
    ARTIFACTS_DIR,
    DEFAULT_STORAGE_STATE,
    execute_plan,
)

MAX_ALLOWED_CHANGE_RATIO = 0.20


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Execute a saved ebooking execution plan.")
    parser.add_argument("--plan-file", help="Saved plan JSON path. Defaults to the latest plan artifact.")
    parser.add_argument("--storage-state", default=str(DEFAULT_STORAGE_STATE), help="Playwright storage-state path.")
    parser.add_argument(
        "--source-room-name",
        action="append",
        default=[],
        help="Optional source room name filter. Can be repeated.",
    )
    parser.add_argument(
        "--room-product-id",
        action="append",
        default=[],
        help="Optional roomProductId filter. Can be repeated.",
    )
    parser.add_argument(
        "--max-ops",
        type=int,
        default=20,
        help="Refuse to execute more than this many operations.",
    )
    parser.add_argument("--wait-seconds", type=int, default=30, help="Task polling timeout per submission.")
    parser.add_argument("--commit", action="store_true", help="Actually submit the saved plan.")
    return parser.parse_args()


def find_latest_plan() -> Path:
    files = sorted(ARTIFACTS_DIR.glob("ebooking_execution_plan_*.json"), reverse=True)
    if not files:
        raise FileNotFoundError("no saved ebooking execution plan found")
    return files[0]


def load_plan(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def compute_plan_digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()[:16]


def filter_plan(
    plan: dict[str, Any],
    source_room_names: list[str],
    room_product_ids: list[str],
) -> dict[str, Any]:
    source_room_name_set = set(source_room_names)
    room_product_id_set = set(room_product_ids)
    filtered = deepcopy(plan)
    filtered_groups: list[dict[str, Any]] = []
    skipped_groups: list[dict[str, Any]] = list(filtered.get("skippedGroups", []))

    for group in filtered.get("planGroups", []):
        if source_room_name_set and group["sourceRoomName"] not in source_room_name_set:
            continue

        operations = []
        operation_skips = list(group.get("operationSkips", []))
        for operation in group.get("operations", []):
            if room_product_id_set and operation["roomProductId"] not in room_product_id_set:
                operation_skips.append(
                    {
                        "roomProductId": operation["roomProductId"],
                        "reason": "filtered out by room-product-id selector",
                    }
                )
                continue
            operations.append(operation)

        if not operations:
            skipped_groups.append(
                {
                    "sourceRoomName": group["sourceRoomName"],
                    "reason": "no operations left after filters",
                    "operationSkips": operation_skips,
                }
            )
            continue

        group["operations"] = operations
        group["operationSkips"] = operation_skips
        filtered_groups.append(group)

    filtered["planGroups"] = filtered_groups
    filtered["skippedGroups"] = skipped_groups
    return filtered


def validate_plan(plan: dict[str, Any], max_ops: int) -> list[str]:
    errors: list[str] = []
    total_ops = 0
    for group in plan.get("planGroups", []):
        for operation in group.get("operations", []):
            total_ops += 1
            current_sale = float(operation["currentEbookingSalePrice"])
            target_sale = float(operation["targetSalePrice"])
            target_cost = float(operation["targetCostPrice"])

            if current_sale <= 0:
                errors.append(f"{group['sourceRoomName']} {operation['roomProductId']}: invalid current sale price")
                continue
            if target_sale <= 0:
                errors.append(f"{group['sourceRoomName']} {operation['roomProductId']}: target sale price must be positive")
            if target_cost < 0:
                errors.append(f"{group['sourceRoomName']} {operation['roomProductId']}: target cost price must be non-negative")
            if target_cost > target_sale:
                errors.append(
                    f"{group['sourceRoomName']} {operation['roomProductId']}: target cost price exceeds target sale price"
                )

            change_ratio = abs(target_sale - current_sale) / current_sale
            if change_ratio > MAX_ALLOWED_CHANGE_RATIO + 1e-9:
                errors.append(
                    f"{group['sourceRoomName']} {operation['roomProductId']}: target sale price change "
                    f"{change_ratio:.2%} exceeds {MAX_ALLOWED_CHANGE_RATIO:.0%} guardrail"
                )

    if total_ops == 0:
        errors.append("no operations available after filters")
    if total_ops > max_ops:
        errors.append(f"operation count {total_ops} exceeds --max-ops {max_ops}")
    return errors


def summarize_plan(plan: dict[str, Any]) -> dict[str, Any]:
    groups = plan.get("planGroups", [])
    operations = [operation for group in groups for operation in group.get("operations", [])]
    return {
        "dateRange": plan.get("dateRange", {}),
        "groupCount": len(groups),
        "operationCount": len(operations),
        "sourceRoomNames": [group["sourceRoomName"] for group in groups],
        "roomProductIds": [operation["roomProductId"] for operation in operations],
    }


def save_execution_result(payload: dict[str, Any]) -> Path:
    ARTIFACTS_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    path = ARTIFACTS_DIR / f"ebooking_execution_result_{stamp}.json"
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def main() -> int:
    args = parse_args()
    plan_file = Path(args.plan_file).expanduser() if args.plan_file else find_latest_plan()
    plan = load_plan(plan_file)
    filtered_plan = filter_plan(plan, args.source_room_name, args.room_product_id)
    summary = summarize_plan(filtered_plan)
    validation_errors = validate_plan(filtered_plan, args.max_ops)

    output: dict[str, Any] = {
        "planFile": str(plan_file),
        "summary": summary,
        "commit": args.commit,
        "filters": {
            "sourceRoomNames": args.source_room_name,
            "roomProductIds": args.room_product_id,
            "maxOps": args.max_ops,
        },
        "filteredPlan": filtered_plan,
    }

    if validation_errors:
        output["validationErrors"] = validation_errors
        print(json.dumps(output, ensure_ascii=False, indent=2))
        return 1

    if not args.commit:
        output["note"] = "Dry run only. Re-run with --commit to execute the saved plan."
        print(json.dumps(output, ensure_ascii=False, indent=2))
        return 0

    client = EBookingBatchPriceClient(Path(args.storage_state).expanduser())
    execution_result = execute_plan(client, filtered_plan, args.wait_seconds)
    result_payload = {
        "generatedAt": datetime.now().isoformat(),
        "sourcePlanFile": str(plan_file),
        "summary": summary,
        "executionResult": execution_result,
    }
    result_path = save_execution_result(result_payload)
    output["resultPath"] = str(result_path)
    output["executionResult"] = execution_result
    print(json.dumps(output, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
