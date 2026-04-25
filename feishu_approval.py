#!/usr/bin/env python3
"""
Poll Feishu DM replies for a saved approval request and optionally execute the approved plan.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any

SCRIPT_DIR = Path(__file__).resolve().parent
PRICING_DIR = SCRIPT_DIR / "pricing"
if str(PRICING_DIR) not in sys.path:
    sys.path.insert(0, str(PRICING_DIR))

from feishu_client import get_tenant_token, list_chat_messages, parse_text_content, reply_text_message
from pricing.execute_saved_plan import (
    DEFAULT_STORAGE_STATE,
    compute_plan_digest,
    filter_plan,
    load_plan,
    save_execution_result,
    summarize_plan,
    validate_plan,
)
from pricing.recommendation_to_execution_plan import ARTIFACTS_DIR, execute_plan
from pricing.ebooking_batch_price_api import EBookingBatchPriceClient


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Check Feishu replies and optionally execute an approved saved plan.")
    parser.add_argument("--dispatch-file", help="Approval request JSON path. Defaults to the latest one.")
    parser.add_argument("--watch-seconds", type=int, default=0, help="Poll until timeout. 0 means check once.")
    parser.add_argument("--poll-interval", type=int, default=10, help="Polling interval in seconds for watch mode.")
    parser.add_argument("--storage-state", default=str(DEFAULT_STORAGE_STATE), help="Playwright storage-state path.")
    parser.add_argument("--max-ops", type=int, default=20, help="Execution guardrail for operation count.")
    parser.add_argument("--commit", action="store_true", help="Actually execute the approved plan.")
    parser.add_argument("--notify", action="store_true", help="Reply in Feishu with the current decision or execution result.")
    return parser.parse_args()


def find_latest_dispatch() -> Path:
    files = sorted(ARTIFACTS_DIR.glob("feishu_approval_request_*.json"), reverse=True)
    if not files:
        raise FileNotFoundError("no saved Feishu approval request found")
    return files[0]


def load_dispatch(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def save_dispatch(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def normalize_text(text: str) -> str:
    return "".join(text.split())


def normalize_decision_text(text: str) -> str:
    normalized = normalize_text(text)
    normalized = re.sub(r"^(?:\[引用\]|【引用】|引用[:：]?|回复[:：]?)", "", normalized)
    return normalized.strip("。.!！,，;；:：“”\"'（）()[]【】")


def sender_open_id(message: dict[str, Any]) -> str:
    sender_id = message.get("sender", {}).get("sender_id", {})
    if isinstance(sender_id, dict):
        return sender_id.get("open_id", "")
    return ""


def matches_decision_keyword(text: str, keywords: list[str]) -> bool:
    normalized = normalize_decision_text(text)
    if normalized in keywords:
        return True

    candidate = normalized
    for prefix in ("好的", "好", "可以", "行", "收到", "麻烦", "请"):
        if candidate.startswith(prefix):
            candidate = candidate[len(prefix) :].strip("。.!！,，;；:：“”\"'（）()[]【】")
            break
    for suffix in ("谢谢", "辛苦", "可以", "吧", "了", "哈", "哦", "呀"):
        if candidate.endswith(suffix):
            candidate = candidate[: -len(suffix)].strip("。.!！,，;；:：“”\"'（）()[]【】")
            break
    return candidate in keywords


def detect_decision(dispatch: dict[str, Any], messages: list[dict[str, Any]]) -> dict[str, Any] | None:
    approval_keywords = [normalize_decision_text(item) for item in dispatch.get("approvalKeywords", [])]
    reject_keywords = [normalize_decision_text(item) for item in dispatch.get("rejectKeywords", [])]
    expected_user_open_id = dispatch.get("user_open_id") or dispatch.get("receiverOpenId") or dispatch.get("approverOpenId")
    sent_message = dispatch.get("feishuMessage", {})
    sent_create_time = int(sent_message.get("create_time", "0"))
    sent_message_id = sent_message.get("message_id")
    next_app_message_time = None

    for message in messages:
        if message.get("message_id") == sent_message_id:
            continue
        if message.get("sender", {}).get("sender_type") != "app":
            continue
        create_time = int(message.get("create_time", "0"))
        if create_time <= sent_create_time:
            continue
        if next_app_message_time is None or create_time < next_app_message_time:
            next_app_message_time = create_time

    latest_match: dict[str, Any] | None = None
    for message in messages:
        if message.get("message_id") == sent_message_id:
            continue
        message_create_time = int(message.get("create_time", "0"))
        if message_create_time <= sent_create_time:
            continue
        if next_app_message_time is not None and message_create_time >= next_app_message_time:
            continue
        if message.get("sender", {}).get("sender_type") != "user":
            continue
        if expected_user_open_id and sender_open_id(message) != expected_user_open_id:
            continue

        text = parse_text_content(message)
        if not text:
            continue
        decision = None
        if matches_decision_keyword(text, approval_keywords):
            decision = "approved"
        elif matches_decision_keyword(text, reject_keywords):
            decision = "rejected"

        if decision is None:
            continue

        latest_match = {
            "decision": decision,
            "messageId": message.get("message_id"),
            "createTime": message.get("create_time"),
            "chatId": message.get("chat_id"),
            "text": text,
            "sender": message.get("sender", {}),
        }
    return latest_match


def fetch_recent_messages(token: str, chat_id: str, start_time_ms: int) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    page_token = None
    while True:
        data = list_chat_messages(
            token,
            chat_id,
            start_time=max(0, (start_time_ms // 1000) - 60),
            sort_type="ByCreateTimeAsc",
            page_size=50,
            page_token=page_token,
        )
        batch = data.get("data", {}).get("items", [])
        items.extend(batch)
        page_token = data.get("data", {}).get("page_token")
        has_more = data.get("data", {}).get("has_more", False)
        if not has_more or not page_token:
            break
    return items


def build_status_message(dispatch: dict[str, Any]) -> str:
    summary = dispatch.get("executionSummary") or {}
    status = dispatch.get("status", "pending")
    if status == "approved":
        return "已收到“确认改价”，审批状态已更新为通过，等待执行。"
    if status == "rejected":
        return "已收到拒绝指令，本次改价计划不会执行。"
    if status == "executed":
        return (
            "已执行改价计划："
            f"{summary.get('groupCount', 0)}组房型，{summary.get('operationCount', 0)}个子产品。"
        )
    if status == "execution_failed":
        return f"改价执行失败：{dispatch.get('executionError', '未知错误')}"
    if status == "duplicate_blocked":
        return "该改价计划已在另一条审批链路中执行，本条审批已被自动拦截，避免重复改价。"
    return "审批仍在等待中，尚未检测到明确的确认或拒绝回复。"


def execute_dispatch(dispatch: dict[str, Any], storage_state: str, max_ops: int) -> dict[str, Any]:
    plan_file = dispatch.get("planFile")
    if not plan_file:
        raise RuntimeError("dispatch record does not include a plan file")

    plan = load_plan(Path(plan_file))
    filtered_plan = filter_plan(plan, [], [])
    errors = validate_plan(filtered_plan, max_ops)
    if errors:
        raise RuntimeError("; ".join(errors))

    client = EBookingBatchPriceClient(Path(storage_state).expanduser())
    result = execute_plan(client, filtered_plan, 30)
    return {
        "summary": summarize_plan(filtered_plan),
        "result": result,
    }


def maybe_notify(token: str, dispatch: dict[str, Any], *, previous_status: str | None) -> bool:
    current_status = dispatch.get("status")
    sent_message = dispatch.get("feishuMessage", {})
    message_id = sent_message.get("message_id")
    if not message_id:
        return False
    if current_status == "pending":
        return False
    if previous_status == current_status and dispatch.get("lastNotifiedStatus") == current_status:
        return False
    reply_text_message(token, message_id, build_status_message(dispatch))
    dispatch["lastNotifiedStatus"] = current_status
    dispatch["lastNotifiedAt"] = datetime.now().isoformat()
    return True


def ensure_plan_digest(dispatch: dict[str, Any]) -> str | None:
    if dispatch.get("planDigest"):
        return dispatch["planDigest"]
    plan_file = dispatch.get("planFile")
    if not plan_file:
        return None
    digest = compute_plan_digest(Path(plan_file))
    dispatch["planDigest"] = digest
    return digest


def find_existing_plan_execution(dispatch_file: Path, plan_digest: str | None) -> Path | None:
    if not plan_digest:
        return None
    for candidate in sorted(ARTIFACTS_DIR.glob("feishu_approval_request_*.json"), reverse=True):
        if candidate == dispatch_file:
            continue
        payload = load_dispatch(candidate)
        if payload.get("status") != "executed":
            continue
        other_digest = payload.get("planDigest")
        if other_digest is None and payload.get("planFile"):
            other_digest = compute_plan_digest(Path(payload["planFile"]))
        if other_digest == plan_digest:
            return candidate
    return None


def process_dispatch_file(
    dispatch_file: Path,
    *,
    storage_state: str,
    max_ops: int,
    commit: bool,
    notify: bool,
    watch_seconds: int = 0,
    poll_interval: int = 10,
) -> dict[str, Any]:
    dispatch = load_dispatch(dispatch_file)
    plan_digest = ensure_plan_digest(dispatch)
    token = get_tenant_token()

    sent_message = dispatch.get("feishuMessage", {})
    chat_id = sent_message.get("chat_id")
    create_time = int(sent_message.get("create_time", "0"))
    if not chat_id or not create_time:
        raise RuntimeError("dispatch record does not include chat_id/create_time")

    previous_status = dispatch.get("status")
    deadline = time.time() + watch_seconds
    decision = None
    while True:
        messages = fetch_recent_messages(token, chat_id, create_time)
        decision = detect_decision(dispatch, messages)
        dispatch["lastCheckedAt"] = datetime.now().isoformat()
        dispatch["historySampleCount"] = len(messages)
        if decision is not None:
            dispatch["decision"] = decision
            dispatch["status"] = decision["decision"]
            save_dispatch(dispatch_file, dispatch)
            break
        if watch_seconds <= 0 or time.time() >= deadline:
            save_dispatch(dispatch_file, dispatch)
            break
        time.sleep(max(1, poll_interval))

    if dispatch.get("status") == "approved" and commit and dispatch.get("executedAt") is None:
        duplicate = find_existing_plan_execution(dispatch_file, plan_digest)
        if duplicate is not None:
            dispatch["status"] = "duplicate_blocked"
            dispatch["duplicateOf"] = str(duplicate)
            save_dispatch(dispatch_file, dispatch)
            if notify:
                if maybe_notify(token, dispatch, previous_status=previous_status):
                    save_dispatch(dispatch_file, dispatch)
            return dispatch
        try:
            execution = execute_dispatch(dispatch, storage_state, max_ops)
            dispatch["status"] = "executed"
            dispatch["executedAt"] = datetime.now().isoformat()
            dispatch["executionSummary"] = execution["summary"]
            dispatch["executionResult"] = execution["result"]
            dispatch["planDigest"] = plan_digest
            dispatch["executionArtifact"] = str(
                save_execution_result(
                    {
                        "generatedAt": datetime.now().isoformat(),
                        "sourceDispatchFile": str(dispatch_file),
                        "planDigest": plan_digest,
                        "summary": execution["summary"],
                        "executionResult": execution["result"],
                    }
                )
            )
        except Exception as exc:  # noqa: BLE001
            dispatch["status"] = "execution_failed"
            dispatch["executionError"] = str(exc)
        save_dispatch(dispatch_file, dispatch)

    if notify:
        if maybe_notify(token, dispatch, previous_status=previous_status):
            save_dispatch(dispatch_file, dispatch)

    return dispatch


def main() -> int:
    args = parse_args()
    dispatch_file = Path(args.dispatch_file).expanduser() if args.dispatch_file else find_latest_dispatch()
    dispatch = process_dispatch_file(
        dispatch_file,
        storage_state=args.storage_state,
        max_ops=args.max_ops,
        commit=args.commit,
        notify=args.notify,
        watch_seconds=args.watch_seconds,
        poll_interval=args.poll_interval,
    )

    print(json.dumps({"dispatchFile": str(dispatch_file), "dispatch": dispatch}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
