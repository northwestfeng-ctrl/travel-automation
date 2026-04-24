#!/usr/bin/env python3
"""
Charles代理抓包 - 通过Charles捕获ebooking请求
"""
import json
import time
from playwright.sync_api import sync_playwright

try:
    from path_config import captured_requests_path
except ModuleNotFoundError:
    from pricing.path_config import captured_requests_path

CAPTURED_FILE = captured_requests_path("capture_proxy")
KEYWORDS = ('rateplan', 'price', 'roomtype', 'setting', 'batch', 'update', 'save', 'hotelid')

captured_requests = []


def is_interesting_request(url):
    lowered = url.lower()
    return "ebooking.ctrip.com" in lowered and any(kw in lowered for kw in KEYWORDS)

def main():
    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=False,
            proxy={
                "server": "http://localhost:8888",
            }
        )
        context = browser.new_context(
            proxy={"server": "http://localhost:8888"}
        )
        page = context.new_page()

        def on_request(request):
            url = request.url
            if not is_interesting_request(url):
                return
            captured_requests.append({
                "url": url,
                "method": request.method,
                "headers": dict(request.headers),
                "post_data": request.post_data,
                "timestamp": time.time()
            })
            print(f"[{request.method}] {url[:120]}")

        def on_response(response):
            url = response.url
            if any(kw in url.lower() for kw in ['rateplan', 'price', 'roomtype', 'hotel', 'setting', 'batch', 'update', 'save']):
                print(f"  → {response.status} [{response.headers.get('content-length', '?')} bytes]")

        page.on("request", on_request)
        page.on("response", on_response)

        print("=" * 70)
        print("Charles 代理抓包已启动")
        print("请在浏览器中手动操作 ebooking，完成后关闭窗口")
        print("=" * 70)

        page.goto("https://ebooking.ctrip.com/ebooking/")

        # 等待60秒后自动结束
        time.sleep(60)

        with open(CAPTURED_FILE, 'w', encoding='utf-8') as f:
            json.dump(captured_requests, f, ensure_ascii=False, indent=2)

        print(f"\n\n共捕获 {len(captured_requests)} 个请求，已保存")
        browser.close()

if __name__ == "__main__":
    main()
