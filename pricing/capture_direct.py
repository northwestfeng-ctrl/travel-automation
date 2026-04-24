#!/usr/bin/env python3
"""
直接抓取 ebooking 改价接口（无需代理）
5分钟抓取窗口，监听所有 XHR/fetch 请求
"""
import json
import time
import threading
from playwright.sync_api import sync_playwright

try:
    from path_config import captured_requests_path
except ModuleNotFoundError:
    from pricing.path_config import captured_requests_path

CAPTURED_FILE = captured_requests_path("capture_direct")
DURATION_SECONDS = 300  # 5分钟

captured_requests = []
stop_capture = False

def main():
    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=False,
            args=['--disable-blink-features=AutomationControlled']
        )
        context = browser.new_context(
            viewport={"width": 1280, "height": 800}
        )
        page = context.new_page()

        def on_request(request):
            url = request.url
            if any(kw in url.lower() for kw in ['rateplan', 'price', 'roomtype', 'hotel', 'setting', 'batch', 'update', 'save']):
                req_data = {
                    "url": url,
                    "method": request.method,
                    "headers": dict(request.headers),
                    "post_data": request.post_data,
                    "timestamp": time.time()
                }
                captured_requests.append(req_data)
                print(f"[{request.method}] {url[:100]}")

        def on_response(response):
            url = response.url
            if any(kw in url.lower() for kw in ['rateplan', 'price', 'roomtype', 'hotel', 'setting', 'batch', 'update']):
                print(f"  → {response.status}")

        page.on("request", on_request)
        page.on("response", on_response)

        print("=" * 60)
        print(f"开始抓取 {DURATION_SECONDS} 秒")
        print("请在浏览器中操作 ebooking，完成后关闭窗口")
        print("或等待 5 分钟后自动结束")
        print("=" * 60)

        page.goto("https://ebooking.ctrip.com/ebooking/")

        # 等待固定时间
        time.sleep(DURATION_SECONDS)

        # 保存结果
        with open(CAPTURED_FILE, 'w', encoding='utf-8') as f:
            json.dump(captured_requests, f, ensure_ascii=False, indent=2)

        print(f"\n已保存 {len(captured_requests)} 个请求到 {CAPTURED_FILE}")

        # 打印所有请求详情
        for req in captured_requests:
            print(f"\n{'='*60}")
            print(f"URL: {req['url']}")
            print(f"Method: {req['method']}")
            print(f"Headers: {json.dumps(req['headers'], ensure_ascii=False)[:500]}")
            if req.get('post_data'):
                print(f"Body: {req['post_data']}")

        browser.close()

if __name__ == "__main__":
    main()
