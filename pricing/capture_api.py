#!/usr/bin/env python3
"""
抓取携程ebooking改价接口
使用方式：
1. 确保Charles正在运行（系统代理模式）
2. 运行脚本：python3 capture_api.py
3. 在打开的浏览器中手动操作：登录ebooking → 找到任意房型 → 修改价格 → 保存
4. 脚本会捕获所有HTTP请求，保存到 captured_requests.json
5. 操作完成后按 Enter 结束抓取
"""
import json
import time
import sys
from playwright.sync_api import sync_playwright

try:
    from path_config import captured_requests_path
except ModuleNotFoundError:
    from pricing.path_config import captured_requests_path

CAPTURED_FILE = captured_requests_path("capture_api")

# 存储捕获的请求
captured_requests = []

def main():
    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=False,
            proxy={
                "server": "http://localhost:8888",
            }
        )
        context = browser.new_context(
            proxy={
                "server": "http://localhost:8888",
            }
        )
        page = context.new_page()

        # 监听所有请求
        def on_request(request):
            # 只记录API请求（包含 api/ebooking 或 rateplan 等关键词）
            url = request.url.lower()
            if any(kw in url for kw in ['api', 'ebooking', 'rateplan', 'price', 'room', 'hotel']):
                req_data = {
                    "url": request.url,
                    "method": request.method,
                    "headers": dict(request.headers),
                    "post_data": request.post_data,
                    "timestamp": time.time()
                }
                captured_requests.append(req_data)
                print(f"[CAPTURED] {request.method} {request.url[:100]}")

        page.on("request", on_request)

        print("=" * 60)
        print("Charles 代理抓包已启动")
        print("请在浏览器中手动操作 ebooking 改价")
        print("操作完成后按 Enter 结束抓取...")
        print("=" * 60)

        # 等待用户操作
        page.goto("https://ebooking.ctrip.com/ebooking/#/hotel/list")
        input()

        # 保存结果
        with open(CAPTURED_FILE, 'w', encoding='utf-8') as f:
            json.dump(captured_requests, f, ensure_ascii=False, indent=2)

        print(f"\n已保存 {len(captured_requests)} 个请求到 {CAPTURED_FILE}")

        # 打印关键发现
        print("\n=== 关键请求 ===")
        for req in captured_requests:
            if any(kw in req['url'].lower() for kw in ['price', 'rateplan', 'rate', 'set']):
                print(f"\nURL: {req['url']}")
                print(f"Method: {req['method']}")
                if req.get('post_data'):
                    print(f"Body: {req['post_data'][:500]}")

        browser.close()

if __name__ == "__main__":
    main()
