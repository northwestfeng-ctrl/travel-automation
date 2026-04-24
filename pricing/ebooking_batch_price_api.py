#!/usr/bin/env python3
"""
Direct ebooking batch-price client based on the live /rateplan/batchPriceSetting flow.

Default mode is read-only. Use --commit explicitly for write operations.
"""
from __future__ import annotations

import argparse
import json
import os
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

import requests

try:
    from path_config import DEFAULT_STORAGE_STATE_PATH
except ModuleNotFoundError:
    from pricing.path_config import DEFAULT_STORAGE_STATE_PATH

DEFAULT_BATCH_PAGE = "/rateplan/batchPriceSetting"
DEFAULT_RESULT_PAGE = "/rateplan/batchSetTaskResult"
DEFAULT_PAGE_ID = "10650010602"
DEFAULT_RESULT_PAGE_ID = "10650164492"
ALL_WEEK_DAYS = [
    "MONDAY",
    "TUESDAY",
    "WEDNESDAY",
    "THURSDAY",
    "FRIDAY",
    "SATURDAY",
    "SUNDAY",
]
NON_TERMINAL_TASK_STATUSES = {
    "INIT",
    "CREATING",
    "RUNNING",
    "PROCESSING",
    "PENDING",
}


@dataclass
class DateRange:
    start_date: str
    end_date: str

    def to_payload(self) -> dict[str, str]:
        return {
            "startDate": self.start_date,
            "endDate": self.end_date,
        }


class EBookingBatchPriceClient:
    def __init__(self, storage_state_path: Path) -> None:
        state = json.loads(storage_state_path.read_text(encoding="utf-8"))
        self.session = requests.Session()
        self.local_storage = self._read_local_storage(state)
        self.guid = self.local_storage["GUID"]

        for cookie in state.get("cookies", []):
            self.session.cookies.set(
                cookie["name"],
                cookie["value"],
                domain=cookie.get("domain"),
                path=cookie.get("path", "/"),
            )

    @staticmethod
    def _read_local_storage(state: dict[str, Any]) -> dict[str, str]:
        for origin in state.get("origins", []):
            if origin.get("origin") != "https://ebooking.ctrip.com":
                continue
            return {
                item["name"]: item["value"]
                for item in origin.get("localStorage", [])
            }
        raise RuntimeError("ebooking localStorage not found in storage-state file")

    def _trace_id(self) -> str:
        return f"{self.guid}-{int(time.time() * 1000)}-1234567"

    def _base_head(self) -> dict[str, Any]:
        return {
            "cid": self.guid,
            "ctok": "",
            "cver": "1.0",
            "lang": "01",
            "sid": "8888",
            "syscode": "09",
            "auth": "",
            "xsid": "",
            "extension": [],
        }

    def _req_head(self, path_name: str, page_id: str) -> dict[str, Any]:
        return {
            "host": "ebooking.ctrip.com",
            "pathName": path_name,
            "locale": "zh-CN",
            "release": "",
            "client": {
                "deviceType": "PC",
                "os": "Mac",
                "osVersion": "",
                "deviceName": "Macintosh",
                "clientId": self.guid,
                "screenWidth": 1440,
                "screenHeight": 960,
                "isIn": {
                    "ie": False,
                    "chrome": True,
                    "chrome49": False,
                    "wechat": False,
                    "firefox": False,
                    "ios": False,
                    "android": False,
                },
                "isModernBrowser": True,
                "browser": "Chrome",
                "browserVersion": "145",
                "platform": "pc",
                "technology": "web",
            },
            "ubt": {
                "pageid": page_id,
                "pvid": 2,
                "sid": 2,
                "vid": self._extract_vid(),
                "fp": self._extract_fp(),
                "rmsToken": "",
            },
            "gps": {
                "coord": "",
                "lat": "",
                "lng": "",
                "cid": 0,
                "cnm": "",
            },
            "protocal": "https:",
        }

    def _extract_vid(self) -> str:
        raw = self.local_storage.get("UBT_LASTVIEW")
        if not raw:
            return ""
        try:
            return json.loads(raw).get("vid", "")
        except json.JSONDecodeError:
            return ""

    def _extract_fp(self) -> str:
        raw = self.local_storage.get("UBT_LASTVIEW")
        if not raw:
            return ""
        try:
            return json.loads(raw).get("fp", "")
        except json.JSONDecodeError:
            return ""

    def _post_soa(
        self,
        service_code: str,
        operation: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        url = (
            f"https://ebooking.ctrip.com/restapi/soa2/{service_code}/{operation}"
            f"?_fxpcqlniredt={self.guid}&x-traceID={self._trace_id()}"
        )
        response = self.session.post(url, json=payload, timeout=30)
        response.raise_for_status()
        data = response.json()
        rcode = data.get("resStatus", {}).get("rcode")
        if rcode not in (None, 200):
            raise RuntimeError(f"{operation} failed: {rcode} {data.get('resStatus', {}).get('rmsg')}")
        return data

    def list_room_products(self) -> dict[str, Any]:
        payload = {
            "reqHead": self._req_head(DEFAULT_BATCH_PAGE, DEFAULT_PAGE_ID),
            "cipher": None,
            "tipsScene": "price",
            "head": self._base_head(),
        }
        return self._post_soa("30535", "getRCRoomProductList", payload)

    def get_room_price_setting(
        self,
        room_product_ids: list[str],
        date_range: DateRange,
        cipher: dict[str, str],
    ) -> dict[str, Any]:
        payload = {
            "reqHead": self._req_head(DEFAULT_BATCH_PAGE, DEFAULT_PAGE_ID),
            "roomProductIds": room_product_ids,
            "dateRanges": [date_range.to_payload()],
            "weekDays": ALL_WEEK_DAYS,
            "withPrice": True,
            "cipher": {pid: cipher[pid] for pid in room_product_ids},
            "head": self._base_head(),
        }
        return self._post_soa("23783", "getRCRoomPriceSetting", payload)

    def set_room_price(
        self,
        room_product_id: str,
        date_range: DateRange,
        sale_price: float,
        cost_price: float,
        commission_rate: float,
        meal_num: int,
        currency: str,
        cipher: dict[str, str],
    ) -> dict[str, Any]:
        payload = {
            "reqHead": self._req_head(DEFAULT_BATCH_PAGE, DEFAULT_PAGE_ID),
            "roomPriceInfos": [
                {
                    "roomProductId": room_product_id,
                    "startDate": date_range.start_date,
                    "endDate": date_range.end_date,
                    "salePrice": sale_price,
                    "costPrice": cost_price,
                    "commissionRate": commission_rate,
                    "priceChangeMode": "sale_commissionRate",
                    "mealNum": meal_num,
                    "weekDays": ALL_WEEK_DAYS,
                    "currency": currency,
                    "excludedRelationRoomProductIds": [],
                }
            ],
            "isFixedCommission": False,
            "dateRanges": [date_range.to_payload()],
            "weekDays": ALL_WEEK_DAYS,
            "priceChangeMode": "priceMode",
            "diffWeekendPrice": False,
            "cipher": {room_product_id: cipher[room_product_id]},
            "head": self._base_head(),
        }
        return self._post_soa("23783", "setRCRoomPrice", payload)

    def query_main_task(self, task_id: str, task_cipher: str | None = None) -> dict[str, Any]:
        cipher: dict[str, str] = {}
        if task_cipher:
            cipher[task_id] = task_cipher
        payload = {
            "reqHead": self._req_head(DEFAULT_RESULT_PAGE, DEFAULT_RESULT_PAGE_ID),
            "taskId": task_id,
            "cipher": cipher,
            "head": self._base_head(),
        }
        return self._post_soa("23783", "queryMainTaskInfoForDisplay", payload)

    def query_sub_tasks(
        self,
        parent_task_id: str,
        room_product_ids: list[str],
        cipher: dict[str, str],
    ) -> dict[str, Any]:
        merged_cipher = {parent_task_id: cipher[parent_task_id]}
        for room_product_id in room_product_ids:
            merged_cipher[room_product_id] = cipher[room_product_id]
        payload = {
            "reqHead": self._req_head(DEFAULT_RESULT_PAGE, DEFAULT_RESULT_PAGE_ID),
            "parentTaskId": parent_task_id,
            "roomProductIds": room_product_ids,
            "cipher": merged_cipher,
            "head": self._base_head(),
        }
        return self._post_soa("23783", "querySubTaskByProductForDisplay", payload)

    def wait_for_task(
        self,
        task_id: str,
        timeout_seconds: int,
        initial_task_cipher: str | None = None,
    ) -> dict[str, Any]:
        deadline = time.time() + timeout_seconds
        task_cipher = initial_task_cipher
        last_result: dict[str, Any] | None = None

        while time.time() < deadline:
            result = self.query_main_task(task_id, task_cipher)
            last_result = result
            main_info = result.get("mainTaskInfoForDisplayInfo", {})
            task_cipher = result.get("cipher", {}).get(task_id, task_cipher)
            status = main_info.get("status")
            if status and status not in NON_TERMINAL_TASK_STATUSES:
                return result
            time.sleep(1.5)

        if last_result is None:
            raise RuntimeError("task polling returned no result")
        return last_result


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Direct ebooking batch-price API client.")
    parser.add_argument(
        "--storage-state",
        default=os.environ.get("CTRIP_STORAGE_STATE", str(DEFAULT_STORAGE_STATE_PATH)),
        help="Playwright storage-state JSON path. Defaults to CTRIP_STORAGE_STATE or the standard credentials path.",
    )

    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("list-products", help="List room products from ebooking.")

    get_price = subparsers.add_parser("get-price", help="Read current price settings.")
    get_price.add_argument("--room-product-id", required=True)
    get_price.add_argument("--start-date", required=True)
    get_price.add_argument("--end-date", required=True)

    set_price = subparsers.add_parser("set-price", help="Submit a batch price change.")
    set_price.add_argument("--room-product-id", required=True)
    set_price.add_argument("--start-date", required=True)
    set_price.add_argument("--end-date", required=True)
    set_price.add_argument("--sale-price", required=True, type=float)
    set_price.add_argument("--cost-price", type=float)
    set_price.add_argument("--commission-rate", type=float)
    set_price.add_argument("--meal-num", type=int)
    set_price.add_argument(
        "--commit",
        action="store_true",
        help="Actually submit the write request. Without this flag the command is dry-run only.",
    )
    set_price.add_argument(
        "--wait-seconds",
        type=int,
        default=20,
        help="How long to poll task status after commit.",
    )

    task_status = subparsers.add_parser("task-status", help="Read a task and its sub-task details.")
    task_status.add_argument("--task-id", required=True)
    task_status.add_argument("--room-product-id", action="append", required=True)
    task_status.add_argument("--task-cipher")
    task_status.add_argument("--room-cipher", action="append", default=[])

    return parser.parse_args()


def build_catalog(product_data: dict[str, Any]) -> list[dict[str, Any]]:
    basic_room_type_map = product_data.get("basicRoomTypeMap", {})
    room_products = product_data.get("roomProducts", {})
    product_cipher = product_data.get("cipher", {})

    catalog: list[dict[str, Any]] = []
    for room_product_id, product in room_products.items():
        master_basic_room_id = product.get("masterBasicRoomId")
        basic_room = basic_room_type_map.get(master_basic_room_id, {})
        catalog.append(
            {
                "roomProductId": room_product_id,
                "productDisplayName": product.get("productDisplayName"),
                "masterBasicRoomId": master_basic_room_id,
                "masterBasicRoomName": basic_room.get("basicRoomName"),
                "subBasicRoomId": product.get("subBasicRoomId"),
                "subHotelId": product.get("subHotelId"),
                "currency": product.get("currency"),
                "payType": product.get("payType"),
                "allDayRoom": product.get("allDayRoom"),
                "giftRoom": product.get("giftRoom"),
                "cipher": product_cipher.get(room_product_id),
            }
        )
    catalog.sort(key=lambda item: item["productDisplayName"] or "")
    return catalog


def build_room_cipher_map(product_data: dict[str, Any]) -> dict[str, str]:
    return product_data.get("cipher", {})


def extract_room_price_preview(price_data: dict[str, Any], room_product_id: str) -> dict[str, Any]:
    room_map = price_data.get("roomPriceSettingMap", {})
    room_info = room_map.get(room_product_id)
    if room_info is None:
        raise RuntimeError(f"room product not found in price response: {room_product_id}")

    first_day = room_info.get("firstDayPriceInfo", {})
    return {
        "roomProductId": room_product_id,
        "commissionRate": room_info.get("commissionRate"),
        "priceChangeMode": room_info.get("priceChangeMode"),
        "firstDayPriceInfo": first_day,
        "priceInfo": room_info.get("priceInfo", []),
    }


def parse_room_cipher_args(room_cipher_args: list[str]) -> dict[str, str]:
    cipher_map: dict[str, str] = {}
    for item in room_cipher_args:
        if "=" not in item:
            raise ValueError(f"invalid --room-cipher value: {item}")
        room_product_id, cipher = item.split("=", 1)
        cipher_map[room_product_id] = cipher
    return cipher_map


def dump_json(data: Any) -> None:
    print(json.dumps(data, ensure_ascii=False, indent=2))


def main() -> int:
    args = parse_args()
    client = EBookingBatchPriceClient(Path(args.storage_state).expanduser())

    if args.command == "list-products":
        product_data = client.list_room_products()
        dump_json(
            {
                "generatedAt": datetime.now().isoformat(),
                "productCount": len(product_data.get("roomProducts", {})),
                "catalog": build_catalog(product_data),
            }
        )
        return 0

    if args.command == "get-price":
        product_data = client.list_room_products()
        room_cipher_map = build_room_cipher_map(product_data)
        date_range = DateRange(args.start_date, args.end_date)
        price_data = client.get_room_price_setting(
            [args.room_product_id],
            date_range,
            room_cipher_map,
        )
        dump_json(
            {
                "generatedAt": datetime.now().isoformat(),
                "dateRange": date_range.to_payload(),
                "roomPrice": extract_room_price_preview(price_data, args.room_product_id),
            }
        )
        return 0

    if args.command == "set-price":
        product_data = client.list_room_products()
        room_cipher_map = build_room_cipher_map(product_data)
        date_range = DateRange(args.start_date, args.end_date)
        price_data = client.get_room_price_setting(
            [args.room_product_id],
            date_range,
            room_cipher_map,
        )
        current = extract_room_price_preview(price_data, args.room_product_id)
        current_first_day = current["firstDayPriceInfo"]

        commission_rate = (
            args.commission_rate
            if args.commission_rate is not None
            else float(current["commissionRate"])
        )
        meal_num = args.meal_num if args.meal_num is not None else int(current_first_day.get("mealNum", 0))
        cost_price = (
            args.cost_price
            if args.cost_price is not None
            else round(float(args.sale_price) * (1 - commission_rate), 2)
        )

        room_info = product_data["roomProducts"][args.room_product_id]
        planned_payload = {
            "roomProductId": args.room_product_id,
            "dateRange": date_range.to_payload(),
            "salePrice": args.sale_price,
            "costPrice": cost_price,
            "commissionRate": commission_rate,
            "mealNum": meal_num,
            "currency": room_info["currency"],
            "currentFirstDayPriceInfo": current_first_day,
            "dryRun": not args.commit,
        }

        if not args.commit:
            dump_json(
                {
                    "generatedAt": datetime.now().isoformat(),
                    "plannedRequest": planned_payload,
                    "note": "Dry run only. Re-run with --commit to submit.",
                }
            )
            return 0

        submit_result = client.set_room_price(
            room_product_id=args.room_product_id,
            date_range=date_range,
            sale_price=args.sale_price,
            cost_price=cost_price,
            commission_rate=commission_rate,
            meal_num=meal_num,
            currency=room_info["currency"],
            cipher=room_cipher_map,
        )
        task_id = submit_result.get("taskId")
        task_cipher = submit_result.get("cipher", {}).get(task_id) if task_id else None

        result: dict[str, Any] = {
            "generatedAt": datetime.now().isoformat(),
            "plannedRequest": planned_payload,
            "submitResult": submit_result,
        }
        if task_id:
            main_task = client.wait_for_task(task_id, args.wait_seconds, task_cipher)
            result["mainTask"] = main_task
            cipher = dict(main_task.get("cipher", {}))
            if task_id in cipher and args.room_product_id in cipher:
                result["subTasks"] = client.query_sub_tasks(
                    task_id,
                    [args.room_product_id],
                    cipher,
                )

        dump_json(result)
        return 0

    if args.command == "task-status":
        cipher = parse_room_cipher_args(args.room_cipher)
        main_task = client.query_main_task(args.task_id, args.task_cipher)
        merged_cipher = dict(main_task.get("cipher", {}))
        merged_cipher.update(cipher)
        missing = [
            room_product_id
            for room_product_id in args.room_product_id
            if room_product_id not in merged_cipher
        ]
        result: dict[str, Any] = {
            "generatedAt": datetime.now().isoformat(),
            "mainTask": main_task,
        }
        if not missing and args.task_id in merged_cipher:
            result["subTasks"] = client.query_sub_tasks(
                args.task_id,
                args.room_product_id,
                merged_cipher,
            )
        else:
            result["subTasks"] = {
                "warning": "missing cipher for some room product ids",
                "missingRoomProductIds": missing,
            }
        dump_json(result)
        return 0

    raise RuntimeError(f"unsupported command: {args.command}")


if __name__ == "__main__":
    raise SystemExit(main())
