#!/usr/bin/env python3
"""
Capture the ebooking batch-price flow with HAR output plus a compact JSON summary.

This is intended for manual, headed investigation of:
  https://ebooking.ctrip.com/rateplan/batchPriceSetting?microJump=true

The HAR is the source of truth. The JSON summary keeps only the requests that
look pricing-related so later debugging is faster.
"""
from __future__ import annotations

import argparse
import json
import os
import time
from datetime import datetime
from pathlib import Path
from typing import Any

from playwright.sync_api import Request, sync_playwright

try:
    from path_config import ARTIFACTS_DIR, DEFAULT_STORAGE_STATE_PATH, PROJECT_ROOT
except ModuleNotFoundError:
    from pricing.path_config import ARTIFACTS_DIR, DEFAULT_STORAGE_STATE_PATH, PROJECT_ROOT

DEFAULT_START_URL = "https://ebooking.ctrip.com/rateplan/batchPriceSetting?microJump=true"
KEYWORDS = (
    "batch",
    "rateplan",
    "price",
    "inventory",
    "room",
    "quantity",
    "policy",
    "task",
    "calendar",
    "commission",
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Capture ebooking batch-price requests and responses into HAR + JSON."
    )
    parser.add_argument("--start-url", default=DEFAULT_START_URL, help="Initial page to open.")
    parser.add_argument(
        "--duration",
        type=int,
        default=300,
        help="How long to keep the browser open for manual interaction, in seconds.",
    )
    parser.add_argument(
        "--storage-state",
        help="Optional Playwright storage-state JSON for a pre-authenticated session.",
    )
    parser.add_argument(
        "--headless",
        action="store_true",
        help="Run headless. Omit this for normal manual debugging.",
    )
    return parser.parse_args()


def make_serializable_headers(headers: dict[str, str]) -> dict[str, str]:
    return {str(key): str(value) for key, value in headers.items()}


def is_interesting(url: str, post_data: str | None = None) -> bool:
    haystack = f"{url}\n{post_data or ''}".lower()
    return any(keyword in haystack for keyword in KEYWORDS)


def truncate_text(value: str | None, limit: int = 4000) -> str | None:
    if value is None:
        return None
    return value if len(value) <= limit else value[:limit] + "\n...[truncated]"


def sniff_json(text: str | None) -> Any:
    if not text:
        return None
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return None


def snapshot_storage(page) -> dict[str, Any]:
    return page.evaluate(
        """() => ({
            localStorage: Object.fromEntries(
                Array.from({ length: window.localStorage.length }, (_, idx) => {
                    const key = window.localStorage.key(idx);
                    return [key, window.localStorage.getItem(key)];
                })
            ),
            sessionStorage: Object.fromEntries(
                Array.from({ length: window.sessionStorage.length }, (_, idx) => {
                    const key = window.sessionStorage.key(idx);
                    return [key, window.sessionStorage.getItem(key)];
                })
            )
        })"""
    )


def resolve_storage_state(storage_state_arg: str | None) -> Path | None:
    if storage_state_arg:
        return Path(storage_state_arg).expanduser()
    configured = os.environ.get("CTRIP_STORAGE_STATE")
    if configured:
        return Path(configured).expanduser()
    if DEFAULT_STORAGE_STATE_PATH.exists():
        return DEFAULT_STORAGE_STATE_PATH
    return None


def main() -> int:
    args = parse_args()
    ARTIFACTS_DIR.mkdir(parents=True, exist_ok=True)
    storage_state_path = resolve_storage_state(args.storage_state)

    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    har_path = ARTIFACTS_DIR / f"batch_price_capture_{stamp}.har"
    summary_path = ARTIFACTS_DIR / f"batch_price_capture_{stamp}.json"
    html_path = ARTIFACTS_DIR / f"batch_price_capture_{stamp}.html"

    interesting_entries: list[dict[str, Any]] = []
    request_index: dict[int, dict[str, Any]] = {}
    console_messages: list[dict[str, str]] = []
    page_errors: list[str] = []

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(
            headless=args.headless,
            args=["--disable-blink-features=AutomationControlled"],
        )
        context_kwargs: dict[str, Any] = {
            "viewport": {"width": 1440, "height": 960},
            "record_har_path": str(har_path),
            "record_har_mode": "full",
            "record_har_content": "embed",
        }
        if storage_state_path:
            context_kwargs["storage_state"] = str(storage_state_path)
        context = browser.new_context(**context_kwargs)
        page = context.new_page()

        def on_console(message) -> None:
            console_messages.append(
                {
                    "type": message.type,
                    "text": message.text,
                }
            )

        def on_page_error(error: Exception) -> None:
            page_errors.append(str(error))

        def on_request(request: Request) -> None:
            if not is_interesting(request.url, request.post_data):
                return

            entry = {
                "url": request.url,
                "method": request.method,
                "resource_type": request.resource_type,
                "timestamp": time.time(),
                "headers": make_serializable_headers(request.headers),
                "post_data": truncate_text(request.post_data),
            }
            request_index[id(request)] = entry
            interesting_entries.append(entry)
            print(f"[request] {request.method} {request.url}")

        def on_request_finished(request: Request) -> None:
            entry = request_index.get(id(request))
            if not entry:
                return

            response = request.response()
            if response is None:
                entry["response"] = {"status": None}
                return

            body_text = None
            try:
                body_text = response.text()
            except Exception as exc:  # noqa: BLE001
                body_text = f"<response body unavailable: {exc}>"

            entry["response"] = {
                "status": response.status,
                "status_text": response.status_text,
                "headers": make_serializable_headers(response.headers),
                "body_text": truncate_text(body_text),
                "body_json": sniff_json(body_text),
            }
            print(f"[response] {response.status} {request.method} {request.url}")

        def on_request_failed(request: Request) -> None:
            entry = request_index.get(id(request))
            if not entry:
                return
            entry["failure"] = request.failure
            print(f"[failed] {request.method} {request.url} -> {request.failure}")

        page.on("console", on_console)
        page.on("pageerror", on_page_error)
        page.on("request", on_request)
        page.on("requestfinished", on_request_finished)
        page.on("requestfailed", on_request_failed)

        print("=" * 72)
        print("ebooking batch-price capture started")
        print(f"Open page: {args.start_url}")
        print(f"Capture window: {args.duration}s")
        print(f"HAR output: {har_path}")
        if storage_state_path:
            print(f"Storage state: {storage_state_path}")
        else:
            print("Storage state: none (manual login may be required)")
        print("Manually login if needed, then perform one complete batch-price save flow.")
        print("=" * 72)

        page.goto(args.start_url, wait_until="domcontentloaded")
        try:
            page.wait_for_load_state("networkidle", timeout=10_000)
        except Exception:  # noqa: BLE001
            pass

        start = time.time()
        while time.time() - start < args.duration:
            if page.is_closed():
                break
            time.sleep(1)

        final_url = None
        storage_snapshot = None
        if not page.is_closed():
            final_url = page.url
            try:
                storage_snapshot = snapshot_storage(page)
            except Exception as exc:  # noqa: BLE001
                storage_snapshot = {"error": str(exc)}
            try:
                html_path.write_text(page.content(), encoding="utf-8")
            except Exception as exc:  # noqa: BLE001
                html_path.write_text(
                    f"<html><body><pre>Failed to capture page HTML: {exc}</pre></body></html>",
                    encoding="utf-8",
                )

        summary = {
            "created_at": datetime.now().isoformat(),
            "start_url": args.start_url,
            "final_url": final_url,
            "duration_seconds": args.duration,
            "storage_state_path": str(storage_state_path) if storage_state_path else None,
            "har_path": str(har_path),
            "html_path": str(html_path),
            "interesting_request_count": len(interesting_entries),
            "interesting_requests": interesting_entries,
            "console_messages": console_messages,
            "page_errors": page_errors,
            "storage_snapshot": storage_snapshot,
        }
        summary_path.write_text(
            json.dumps(summary, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

        context.close()
        browser.close()

    print(f"Saved summary: {summary_path}")
    print(f"Saved HAR: {har_path}")
    print(f"Saved HTML: {html_path}")
    print(f"Interesting requests captured: {len(interesting_entries)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
