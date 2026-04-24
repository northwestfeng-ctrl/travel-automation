#!/usr/bin/env python3
"""
ebooking API 拦截脚本 v2
"""
import json
import time
import threading
from playwright.sync_api import sync_playwright

try:
    from path_config import captured_requests_path
except ModuleNotFoundError:
    from pricing.path_config import captured_requests_path

OUTPUT_FILE = captured_requests_path("capture_api_v2")
KEYWORDS = ('rateplan', 'price', 'roomtype', 'setting', 'batch', 'update', 'save', 'hotelid')
all_requests = []
stop_capture = False


def is_interesting_request(url):
    lowered = url.lower()
    return "ebooking.ctrip.com" in lowered and any(kw in lowered for kw in KEYWORDS)

def main():
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False)
        context = browser.new_context(viewport={"width": 1400, "height": 900})
        page = context.new_page()

        def handle_route(route):
            request = route.request
            url = request.url
            method = request.method

            if is_interesting_request(url):
                req_data = {
                    "url": url,
                    "method": method,
                    "headers": dict(request.headers),
                    "post_data": request.post_data,
                    "timestamp": time.time()
                }
                all_requests.append(req_data)

                print(f"⭐[{method}] {url[:120]}")
                if request.post_data and len(request.post_data) < 800:
                    print(f"        POST: {request.post_data[:300]}")

            route.continue_()

        page.route("**/*", handle_route)

        print("=" * 70)
        print("ebooking API 拦截器已启动")
        print("请在浏览器中完成以下操作：")
        print("  1. 登录 ebooking")
        print("  2. 左侧菜单 → '房态房价' 或 '批量改价'")
        print("  3. 找到房型 → 修改价格 → 保存")
        print("操作完成后关闭浏览器窗口，脚本将自动结束")
        print("=" * 70)

        page.goto("https://ebooking.ctrip.com/ebooking/")

        # 等待60秒或直到浏览器关闭
        time.sleep(60)

        # 保存
        with open(OUTPUT_FILE, 'w', encoding='utf-8') as f:
            json.dump(all_requests, f, ensure_ascii=False, indent=2)

        print(f"\n共捕获 {len(all_requests)} 个请求，已保存")

        price_related = [r for r in all_requests if any(kw in r['url'].lower() for kw in ['rateplan','price','setting','batch','update','save'])]
        print(f"价格相关请求: {len(price_related)}")
        for r in price_related:
            print(f"\n{'='*60}")
            print(f"URL: {r['url']}")
            print(f"Method: {r['method']}")
            if r.get('post_data'):
                print(f"Body: {r['post_data']}")

        browser.close()

if __name__ == "__main__":
    main()
