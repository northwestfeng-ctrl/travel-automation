#!/usr/bin/env python3
"""
Generate conservative ebooking room-mapping suggestions from the live catalog.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from pricing.ebooking_batch_price_api import EBookingBatchPriceClient, build_catalog, DEFAULT_STORAGE_STATE_PATH
from pricing.recommendation_to_execution_plan import ARTIFACTS_DIR, DEFAULT_MAPPING_FILE, load_mapping


SOURCE_GROUP_LABELS = {
    "big_bed_standard": "大床房",
    "big_bed_deluxe": "1张1.8米大床",
    "twin": "2张1.5米双人床",
}

EXCLUDE_KEYWORDS = {
    "hourly": ["钟点房"],
    "breakfast": ["早餐"],
    "family": ["亲子", "家庭"],
    "suite": ["套房"],
    "gift": ["礼盒", "礼包"],
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Suggest ebooking room mappings from the live catalog.")
    parser.add_argument("--storage-state", default=str(DEFAULT_STORAGE_STATE_PATH), help="Playwright storage-state path.")
    parser.add_argument("--mapping-file", default=str(DEFAULT_MAPPING_FILE), help="Current mapping JSON path.")
    return parser.parse_args()


def has_any_keyword(text: str, keywords: list[str]) -> bool:
    return any(keyword in text for keyword in keywords)


def classify_product(name: str) -> tuple[str | None, str]:
    if has_any_keyword(name, EXCLUDE_KEYWORDS["hourly"]):
        return None, "钟点房变体"
    if has_any_keyword(name, EXCLUDE_KEYWORDS["breakfast"]):
        return None, "含早餐变体"
    if has_any_keyword(name, EXCLUDE_KEYWORDS["family"]):
        return None, "亲子/家庭产品线"
    if has_any_keyword(name, EXCLUDE_KEYWORDS["suite"]):
        return None, "套房产品线"
    if has_any_keyword(name, EXCLUDE_KEYWORDS["gift"]):
        return None, "礼盒/礼包变体"

    if "双床" in name or "标间" in name or "双标" in name:
        return "twin", "名称明确包含双床/标间"
    if "超大床" in name:
        return "big_bed_standard", "名称包含超大床，归入大床房保守组"
    if "大床房" in name:
        if "浴缸" in name or "阳台" in name or "高定" in name:
            return "big_bed_deluxe", "名称包含大床房且带高阶卖点，归入1张1.8米大床组"
        return "big_bed_deluxe", "名称明确包含大床房"
    return None, "无法从名称稳定判断"


def build_existing_ids(mapping: dict[str, Any]) -> set[str]:
    ids: set[str] = set()
    for group in mapping.get("groups", []):
        ids.update(group.get("room_product_ids", []))
    return ids


def main() -> int:
    args = parse_args()
    client = EBookingBatchPriceClient(Path(args.storage_state).expanduser())
    mapping = load_mapping(Path(args.mapping_file).expanduser())
    existing_ids = build_existing_ids(mapping)
    product_data = client.list_room_products()
    catalog = build_catalog(product_data)

    grouped_candidates: dict[str, list[dict[str, Any]]] = {
        key: [] for key in SOURCE_GROUP_LABELS
    }
    excluded: list[dict[str, Any]] = []
    unknown: list[dict[str, Any]] = []

    for item in catalog:
        room_product_id = item["roomProductId"]
        display_name = item["productDisplayName"] or ""
        if room_product_id in existing_ids:
            continue

        group_key, rationale = classify_product(display_name)
        row = {
            "roomProductId": room_product_id,
            "productDisplayName": display_name,
            "masterBasicRoomName": item.get("masterBasicRoomName"),
            "currency": item.get("currency"),
            "payType": item.get("payType"),
            "allDayRoom": item.get("allDayRoom"),
            "reason": rationale,
        }
        if group_key is None:
            if rationale == "无法从名称稳定判断":
                unknown.append(row)
            else:
                excluded.append(row)
            continue
        grouped_candidates[group_key].append(row)

    report = {
        "generatedAt": datetime.now().isoformat(),
        "mappingFile": str(Path(args.mapping_file).expanduser()),
        "suggestions": {
            SOURCE_GROUP_LABELS[key]: value
            for key, value in grouped_candidates.items()
            if value
        },
        "excluded": excluded,
        "unknown": unknown,
    }

    ARTIFACTS_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    json_path = ARTIFACTS_DIR / f"ebooking_mapping_suggestions_{stamp}.json"
    md_path = ARTIFACTS_DIR / f"ebooking_mapping_suggestions_{stamp}.md"
    json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    lines = [
        "# ebooking 映射候选",
        f"",
        f"生成时间：{report['generatedAt']}",
        f"当前映射文件：{report['mappingFile']}",
        f"",
    ]
    for label, items in report["suggestions"].items():
        lines.append(f"## {label}")
        for item in items:
            lines.append(f"- {item['roomProductId']} | {item['productDisplayName']} | {item['reason']}")
        lines.append("")

    lines.append("## Excluded")
    for item in excluded:
        lines.append(f"- {item['roomProductId']} | {item['productDisplayName']} | {item['reason']}")
    lines.append("")

    lines.append("## Unknown")
    for item in unknown:
        lines.append(f"- {item['roomProductId']} | {item['productDisplayName']} | {item['reason']}")
    lines.append("")

    md_path.write_text("\n".join(lines), encoding="utf-8")
    print(json.dumps({"jsonPath": str(json_path), "mdPath": str(md_path), "report": report}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
