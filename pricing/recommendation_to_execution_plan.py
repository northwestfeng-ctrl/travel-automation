#!/usr/bin/env python3
"""
Convert a public recommendation markdown into a conservative ebooking execution plan.

This tool does not commit by default. It creates a dry-run plan that can later be
reviewed or executed explicitly with --commit.
"""
from __future__ import annotations

import argparse
import json
import os
import re
from datetime import datetime
from pathlib import Path
from typing import Any

try:
    from ebooking_batch_price_api import DateRange, EBookingBatchPriceClient, build_catalog
    from path_config import ARTIFACTS_DIR, DEFAULT_STORAGE_STATE_PATH, PRICING_DIR, PROJECT_ROOT
except ModuleNotFoundError:
    from pricing.ebooking_batch_price_api import DateRange, EBookingBatchPriceClient, build_catalog
    from pricing.path_config import ARTIFACTS_DIR, DEFAULT_STORAGE_STATE_PATH, PRICING_DIR, PROJECT_ROOT


DEFAULT_MAPPING_FILE = PRICING_DIR / "ebooking_room_mapping.json"
DEFAULT_STORAGE_STATE = DEFAULT_STORAGE_STATE_PATH


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build or execute an ebooking plan from a recommendation markdown.")
    parser.add_argument("--recommendation-file", help="Recommendation markdown path. Defaults to the latest one.")
    parser.add_argument("--mapping-file", default=str(DEFAULT_MAPPING_FILE), help="Room mapping JSON path.")
    parser.add_argument(
        "--storage-state",
        default=os.environ.get("CTRIP_STORAGE_STATE", str(DEFAULT_STORAGE_STATE)),
        help="Playwright storage-state path. Defaults to CTRIP_STORAGE_STATE or the standard credentials path.",
    )
    parser.add_argument("--start-date", required=True, help="Execution start date in YYYY-MM-DD.")
    parser.add_argument("--end-date", required=True, help="Execution end date in YYYY-MM-DD.")
    parser.add_argument("--commit", action="store_true", help="Actually submit mapped changes.")
    parser.add_argument("--wait-seconds", type=int, default=30, help="Task polling timeout per submission.")
    return parser.parse_args()


def find_latest_recommendation() -> Path:
    files = sorted(PRICING_DIR.glob("recommendation_*.md"), reverse=True)
    if not files:
        raise FileNotFoundError("no recommendation markdown found")
    return files[0]


def parse_recommendation_markdown(path: Path) -> list[dict[str, Any]]:
    text = path.read_text(encoding="utf-8")
    sections = re.split(r"^### ", text, flags=re.MULTILINE)
    results: list[dict[str, Any]] = []

    for section in sections[1:]:
        lines = [line.strip() for line in section.strip().splitlines() if line.strip()]
        if not lines:
            continue

        room_name = lines[0]
        current_price = None
        suggested_price = None
        suggested_action = None
        reason = None

        for line in lines[1:]:
            if line.startswith("- 当前售价："):
                match = re.search(r"¥(\d+)", line)
                if match:
                    current_price = int(match.group(1))
            elif line.startswith("- 挂牌价："):
                continue
            elif "建议 **¥" in line:
                match = re.search(r"\*\*(.*?)\*\* → 建议 \*\*¥(\d+)\*\*", line)
                if match:
                    suggested_action = match.group(1)
                    suggested_price = int(match.group(2))
            elif line.startswith("- ") and reason is None and ("竞品" in line or "库存" in line):
                reason = line[2:]

        if current_price is None or suggested_price is None:
            continue

        results.append(
            {
                "sourceRoomName": room_name,
                "currentPublicPrice": current_price,
                "suggestedPublicPrice": suggested_price,
                "action": suggested_action,
                "reason": reason,
            }
        )

    return results


def load_mapping(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def build_mapping_index(mapping: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {
        group["source_room_name"]: group
        for group in mapping.get("groups", [])
    }


def build_reference_price_index(mapping: dict[str, Any]) -> dict[str, float]:
    return {
        room_product_id: float(price)
        for room_product_id, price in mapping.get("reference_ebooking_sale_prices", {}).items()
    }


def round_sale_price(value: float) -> int:
    return int(round(value))


def make_plan(
    client: EBookingBatchPriceClient,
    recommendation_items: list[dict[str, Any]],
    mapping: dict[str, Any],
    date_range: DateRange,
    mapping_file: Path,
) -> dict[str, Any]:
    mapping_index = build_mapping_index(mapping)
    reference_price_index = build_reference_price_index(mapping)
    product_catalog = client.list_room_products()
    catalog_index = {item["roomProductId"]: item for item in build_catalog(product_catalog)}
    room_cipher = product_catalog.get("cipher", {})

    plan_groups: list[dict[str, Any]] = []
    skipped_groups: list[dict[str, Any]] = []

    for item in recommendation_items:
        room_name = item["sourceRoomName"]
        group_mapping = mapping_index.get(room_name)
        if not group_mapping:
            skipped_groups.append(
                {
                    "sourceRoomName": room_name,
                    "reason": "no mapping entry",
                }
            )
            continue

        current_public = float(item["currentPublicPrice"])
        suggested_public = float(item["suggestedPublicPrice"])
        if current_public <= 0:
            skipped_groups.append(
                {
                    "sourceRoomName": room_name,
                    "reason": "invalid current public price",
                }
            )
            continue
        multiplier = suggested_public / current_public

        operations: list[dict[str, Any]] = []
        operation_skips: list[dict[str, Any]] = []
        for room_product_id in group_mapping["room_product_ids"]:
            if room_product_id not in catalog_index:
                operation_skips.append(
                    {
                        "roomProductId": room_product_id,
                        "reason": "room product not present in live ebooking catalog",
                    }
                )
                continue
            if room_product_id not in room_cipher:
                operation_skips.append(
                    {
                        "roomProductId": room_product_id,
                        "reason": "room product cipher missing from live ebooking catalog",
                    }
                )
                continue
            price_data = client.get_room_price_setting([room_product_id], date_range, room_cipher)
            room_price_setting_map = price_data.get("roomPriceSettingMap", {})
            if room_product_id not in room_price_setting_map:
                operation_skips.append(
                    {
                        "roomProductId": room_product_id,
                        "reason": "room product missing from live price-setting response",
                    }
                )
                continue
            room_info = room_price_setting_map[room_product_id]
            first_day = room_info["firstDayPriceInfo"]
            current_ebooking_sale = float(first_day["price"])
            commission_rate = float(room_info["commissionRate"])
            reference_ebooking_sale = reference_price_index.get(room_product_id, current_ebooking_sale)
            target_sale = round_sale_price(reference_ebooking_sale * multiplier)
            target_cost = round(target_sale * (1 - commission_rate), 2)

            catalog = catalog_index[room_product_id]
            operations.append(
                {
                    "roomProductId": room_product_id,
                    "productDisplayName": catalog["productDisplayName"],
                    "masterBasicRoomId": catalog["masterBasicRoomId"],
                    "subBasicRoomId": catalog["subBasicRoomId"],
                    "referenceEbookingSalePrice": reference_ebooking_sale,
                    "currentEbookingSalePrice": current_ebooking_sale,
                    "currentEbookingCostPrice": float(first_day["cost"]),
                    "commissionRate": commission_rate,
                    "targetSalePrice": target_sale,
                    "targetCostPrice": target_cost,
                    "mealNum": int(first_day.get("mealNum", 0)),
                    "currency": catalog["currency"],
                    "priceDriftFromReference": round(current_ebooking_sale - reference_ebooking_sale, 2),
                }
            )

        if not operations:
            skipped_groups.append(
                {
                    "sourceRoomName": room_name,
                    "reason": "all mapped ebooking products were skipped",
                    "operationSkips": operation_skips,
                }
            )
            continue

        plan_groups.append(
            {
                "sourceRoomName": room_name,
                "action": item["action"],
                "reason": item["reason"],
                "mappingConfidence": group_mapping["confidence"],
                "mappingRationale": group_mapping["rationale"],
                "currentPublicPrice": item["currentPublicPrice"],
                "suggestedPublicPrice": item["suggestedPublicPrice"],
                "publicPriceMultiplier": round(multiplier, 6),
                "operations": operations,
                "operationSkips": operation_skips,
            }
        )

    return {
        "generatedAt": datetime.now().isoformat(),
        "dateRange": date_range.to_payload(),
        "mappingFile": str(mapping_file),
        "recommendationSummary": recommendation_items,
        "planGroups": plan_groups,
        "skippedGroups": skipped_groups,
        "unmappedRoomProductIds": mapping.get("unmapped_room_product_ids", []),
    }


def execute_plan(
    client: EBookingBatchPriceClient,
    plan: dict[str, Any],
    wait_seconds: int,
) -> dict[str, Any]:
    product_data = client.list_room_products()
    room_cipher = product_data.get("cipher", {})

    executions: list[dict[str, Any]] = []
    for group in plan["planGroups"]:
        for operation in group["operations"]:
            execution: dict[str, Any] = {
                "sourceRoomName": group["sourceRoomName"],
                "operation": operation,
            }
            try:
                submit_result = client.set_room_price(
                    room_product_id=operation["roomProductId"],
                    date_range=DateRange(
                        plan["dateRange"]["startDate"],
                        plan["dateRange"]["endDate"],
                    ),
                    sale_price=operation["targetSalePrice"],
                    cost_price=operation["targetCostPrice"],
                    commission_rate=operation["commissionRate"],
                    meal_num=operation["mealNum"],
                    currency=operation["currency"],
                    cipher=room_cipher,
                )
                execution["submitResult"] = submit_result
                task_id = submit_result.get("taskId")
                if not task_id:
                    execution["status"] = "pending"
                    execution["error"] = "submit result did not include taskId"
                    executions.append(execution)
                    continue

                execution["status"] = "submitted"
                task_cipher = submit_result.get("cipher", {}).get(task_id)
                main_task = client.wait_for_task(task_id, wait_seconds, task_cipher)
                execution["mainTask"] = main_task
                execution["status"] = main_task.get("taskStatus") or main_task.get("status") or execution["status"]
                cipher = dict(main_task.get("cipher", {}))
                if task_id in cipher and operation["roomProductId"] in cipher:
                    execution["subTasks"] = client.query_sub_tasks(
                        task_id,
                        [operation["roomProductId"]],
                        cipher,
                    )
            except Exception as exc:
                execution["status"] = "failed"
                execution["error"] = str(exc)
            executions.append(execution)

    return {
        "executedAt": datetime.now().isoformat(),
        "executions": executions,
    }


def save_plan_artifacts(plan: dict[str, Any], execution_result: dict[str, Any] | None = None) -> dict[str, str]:
    ARTIFACTS_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    plan_path = ARTIFACTS_DIR / f"ebooking_execution_plan_{stamp}.json"
    plan_path.write_text(json.dumps(plan, ensure_ascii=False, indent=2), encoding="utf-8")

    result_path = None
    if execution_result is not None:
        result_path = ARTIFACTS_DIR / f"ebooking_execution_result_{stamp}.json"
        result_path.write_text(json.dumps(execution_result, ensure_ascii=False, indent=2), encoding="utf-8")

    output = {"planPath": str(plan_path)}
    if result_path is not None:
        output["resultPath"] = str(result_path)
    return output


def main() -> int:
    args = parse_args()
    recommendation_file = Path(args.recommendation_file).expanduser() if args.recommendation_file else find_latest_recommendation()
    mapping_file = Path(args.mapping_file).expanduser()
    mapping = load_mapping(mapping_file)
    recommendation_items = parse_recommendation_markdown(recommendation_file)
    client = EBookingBatchPriceClient(Path(args.storage_state).expanduser())
    date_range = DateRange(args.start_date, args.end_date)

    plan = make_plan(client, recommendation_items, mapping, date_range, mapping_file)
    execution_result = execute_plan(client, plan, args.wait_seconds) if args.commit else None
    artifact_paths = save_plan_artifacts(plan, execution_result)

    output: dict[str, Any] = {
        "recommendationFile": str(recommendation_file),
        "mappingFile": str(mapping_file),
        "dateRange": date_range.to_payload(),
        "plan": plan,
        "artifacts": artifact_paths,
        "commit": args.commit,
    }
    if execution_result is not None:
        output["executionResult"] = execution_result

    print(json.dumps(output, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
