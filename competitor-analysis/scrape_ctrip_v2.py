#!/usr/bin/env python3
"""
携程民宿竞品抓取 - v3 修复版
使用 m.ctrip.com 移动端页面 + Playwright Stealth 模式
"""
from playwright.sync_api import sync_playwright
try:
    from stealth import stealth
    USE_STEALTH = True
except ImportError:
    stealth = None
    USE_STEALTH = False

import json
import re
from datetime import datetime
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
try:
    from path_config import competitor_results_dir
except ModuleNotFoundError:
    spec = spec_from_file_location(
        "competitor_path_config",
        Path(__file__).resolve().parent / "path_config.py",
    )
    path_config = module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(path_config)
    competitor_results_dir = path_config.competitor_results_dir

# ── 携程 cookie（从ebooking手动抓取后填入）──────────
# 建议通过 /pricing/capture_api_v2.py 手动抓取后更新
COOKIES = []  # TODO: 填入有效cookie

COMPETITORS = [
    {"name": "海慢慢·Sea Slowly平潭度假别墅", "hotelId": "122192195"},
]

def get_hotel_room_api_url(hotel_id):
    """构造 m站 房型列表 API URL"""
    ts = int(datetime.now().timestamp() * 1000)
    trace_id = f"090311{ts}"
    return (
        f"https://m.ctrip.com/restapi/soa2/33278/getHotelRoomListInland?"
        f"_fxpcqlniredt={trace_id}&x-traceID={trace_id}-{ts}-0"
    )

def extract_rooms_from_api_response(body_str):
    """从 API JSON 响应中提取房间信息"""
    try:
        data = json.loads(body_str)
        rooms = []
        # 遍历 roomTypeList / productList 等字段
        def walk(obj):
            if isinstance(obj, dict):
                if "price" in obj and "roomName" in obj:
                    rooms.append({
                        "name": obj.get("roomName", ""),
                        "price": obj.get("price"),
                        "original_price": obj.get("originalPrice") or obj.get("price"),
                        "bed_type": obj.get("bedType", ""),
                        "area": obj.get("area", ""),
                        "stock": obj.get("stockDesc", ""),
                    })
                for v in obj.values():
                    walk(v)
            elif isinstance(obj, list):
                for item in obj:
                    walk(item)
        walk(data)
        return rooms
    except:
        return []

def scrape_with_playwright():
    results = {
        "timestamp": datetime.now().isoformat(),
        "competitors": []
    }

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False)
        context = browser.new_context(
            viewport={"width": 390, "height": 844},
            user_agent=(
                "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) "
                "AppleWebKit/605.1.15 (KHTML, like Gecko) "
                "Version/17.0 Mobile/15E148 Safari/604.1"
            ),
            locale="zh-CN",
            timezone_id="Asia/Shanghai",
            geolocation={"latitude": 25.6148, "longitude": 119.7316},
            extra_http_headers={
                "Accept-Language": "zh-CN,zh;q=0.9",
                "Accept": "application/json, text/plain, */*",
            }
        )

        # 添加 cookie（如果已配置）
        if COOKIES:
            context.add_cookies(COOKIES)

        # stealth 模式
        if USE_STEALTH:
            stealth(context, user_agent=None)

        page = context.new_page()
        # 拦截 API 响应
        room_api_responses = []

        def on_response(resp):
            url = resp.url
            if "getHotelRoomListInland" in url or "getHotelRoom" in url:
                try:
                    body = resp.text()
                    room_api_responses.append({"url": url, "body": body})
                except:
                    pass

        page.on("response", on_response)

        for comp in COMPETITORS:
            hotel_id = comp["hotelId"]
            print(f"\n[{comp['name']}]")

            try:
                # 方式1: 移动页面
                page.goto(
                    f"https://m.ctrip.com/webapp/hotels/xtaro/detail?hotelid={hotel_id}",
                    wait_until="domcontentloaded",
                    timeout=30000
                )
                # 等待房间数据加载（JS渲染）
                page.wait_for_timeout(15000)

                # 滚动触发 lazy load
                for _ in range(5):
                    page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                    page.wait_for_timeout(3000)

                # 方式2: 如果 API 拦截有数据
                for api_resp in room_api_responses:
                    rooms = extract_rooms_from_api_response(api_resp["body"])
                    if rooms:
                        print(f"  [API] 找到 {len(rooms)} 个房型")
                        for r in rooms:
                            print(f"    - {r['name']}: ¥{r['price']}")
                        results["competitors"].append({
                            "name": comp["name"],
                            "hotelId": hotel_id,
                            "rooms": rooms
                        })
                        room_api_responses.clear()
                        break
                else:
                    # 方式3: DOM 提取（备选）
                    rooms = page.evaluate("""
                    () => {
                        // 查找价格数字
                        const body = document.body.innerText;
                        const results = [];
                        const priceMatches = body.match(/¥\\s*(\\d+)/g);
                        if (priceMatches && priceMatches.length > 0) {
                            // 找房型名
                            const roomBlocks = document.querySelectorAll('[class*="room"], [class*="product"], [class*="item"]');
                            roomBlocks.forEach(block => {
                                const text = block.innerText || '';
                                if (text.includes('¥') && text.length < 2000) {
                                    const priceM = text.match(/¥\\s*(\\d+)/);
                                    const nameM = text.match(/([^\n¥][^¥]{2,30})/);
                                    if (priceM) {
                                        results.push({
                                            name: nameM ? nameM[1].trim() : '未知房型',
                                            price: parseInt(priceM[1]),
                                            original_price: parseInt(priceM[1]),
                                            bed_type: '',
                                            area: null,
                                            stock: text.includes('仅剩') ? '仅剩N间' : ''
                                        });
                                    }
                                }
                            });
                        }
                        return results.slice(0, 15);
                    }
                    """)
                    if rooms:
                        print(f"  [DOM] 找到 {len(rooms)} 个房型")
                        for r in rooms:
                            print(f"    - {r.get('name','?')}: ¥{r.get('price','?')}")
                        results["competitors"].append({
                            "name": comp["name"],
                            "hotelId": hotel_id,
                            "rooms": rooms
                        })
                    else:
                        print(f"  ✗ 未能提取到房型数据")
                        results["competitors"].append({
                            "name": comp["name"],
                            "hotelId": hotel_id,
                            "rooms": []
                        })
                room_api_responses.clear()

            except Exception as e:
                print(f"  抓取失败: {e}")
                import traceback; traceback.print_exc()
                results["competitors"].append({
                    "name": comp["name"],
                    "hotelId": hotel_id,
                    "rooms": [],
                    "error": str(e)
                })

        browser.close()

    # 保存
    results_dir = competitor_results_dir()
    results_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_file = results_dir / f"{ts}_full.json"
    with out_file.open("w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)
    print(f"\n结果已保存: {out_file}")
    return results

if __name__ == "__main__":
    print("=" * 60)
    print("携程竞品抓取 v3 (m站 + API拦截)")
    print("=" * 60)
    scrape_with_playwright()
