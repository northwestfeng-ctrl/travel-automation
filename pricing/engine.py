#!/usr/bin/env python3
"""
携程民宿动态调价引擎 v3
适配 scrape_ctrip.py v2 输出格式
"""
import json
import re
from datetime import datetime
import os
from pathlib import Path


PRICING_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = PRICING_DIR.parent
DEFAULT_RESULTS_DIR = PROJECT_ROOT / "competitor-analysis" / "results"

CONFIG = {
    "price_change_limit_pct": 0.20,  # 最大调价幅度 ±20%
    "comp_name": "海慢慢·Sea Slowly平潭度假别墅",
    "yinwei_name": "因为旅行·IWAY艺墅",
}


def get_basic_type(room_name):
    """从房间名提取基础房型"""
    cats = ["大床房", "双床房", "亲子房", "套房", "子母房", "家庭房", "单间"]
    for t in cats:
        if t in room_name:
            return t
    return "其他"


def get_bed_info(room_name):
    """提取床型信息"""
    m = re.search(r'(\d+)张?([\d.]+)米?(大床|双人床|单人床|沙发床)', room_name)
    if m:
        return m.group(0)
    return ""


def extract_stock_num(s):
    """提取库存数量"""
    if not s:
        return None
    m = re.search(r'(\d+)', s)
    return int(m.group(1)) if m else None


def load_latest_data(results_dir=None):
    """加载最新抓取数据"""
    results_path = Path(
        results_dir
        or os.environ.get("COMPETITOR_RESULTS_DIR")
        or DEFAULT_RESULTS_DIR
    ).expanduser()

    if not results_path.is_dir():
        raise FileNotFoundError(
            f"竞品抓取结果目录不存在: {results_path}. "
            "请先运行抓取脚本，或设置 COMPETITOR_RESULTS_DIR 指向 *_full.json 所在目录。"
        )

    files = [f.name for f in results_path.iterdir() if f.name.endswith('_full.json')]
    if not files:
        raise FileNotFoundError(
            f"未找到抓取结果文件: {results_path} 中没有 *_full.json"
        )

    files.sort(reverse=True)
    latest = results_path / files[0]

    with latest.open('r', encoding='utf-8') as f:
        return json.load(f), files[0]


def match_rooms(comp_rooms, yinwei_rooms):
    """匹配合适的竞品房间"""
    matches = []

    for y_room in yinwei_rooms:
        y_name = y_room["name"]
        y_cat = get_basic_type(y_name)
        y_bed = get_bed_info(y_name)
        y_price = y_room.get("price_low") or y_room.get("price", 0)
        y_original = y_room.get("original_price") or y_room.get("price_high", y_price)
        y_stock = y_room.get("stock", "")

        # 找同类别的竞品房间
        candidates = []
        for c_room in comp_rooms:
            c_name = c_room["name"]
            c_cat = get_basic_type(c_name)

            if c_cat != y_cat:
                continue

            c_price = c_room.get("price_low") or c_room.get("price", 0)
            c_original = c_room.get("original_price") or c_room.get("price_high", c_price)
            c_stock = c_room.get("stock", "")
            c_bed = get_bed_info(c_name)

            # 价格接近度
            denominator = max(y_price, c_price, 1)
            price_diff = abs(c_price - y_price) / denominator

            # 床型匹配度
            bed_match = 1.0 if y_bed == c_bed else 0.5

            # 综合得分
            score = (1 - price_diff) * 0.6 + bed_match * 0.4

            candidates.append({
                "room": c_room,
                "price": c_price,
                "original": c_original,
                "stock": c_stock,
                "score": score,
                "bed": c_bed
            })

        if candidates:
            candidates.sort(key=lambda x: -x["score"])
            best = candidates[0]

            matches.append({
                "yinwei": y_room,
                "comp": best["room"],
                "comp_price": best["price"],
                "comp_original": best["original"],
                "comp_stock": best["stock"],
                "match_score": best["score"]
            })
        else:
            matches.append({
                "yinwei": y_room,
                "comp": None,
                "comp_price": None,
                "comp_original": None,
                "comp_stock": None,
                "match_score": 0
            })

    return matches


def calc_adjustment(y_price, y_original, c_price, c_original, y_stock):
    """计算调价建议"""
    y_price = y_price or 0
    if c_price is None:
        return "维持", y_price, "无同类竞品参考", "0%"
    if y_price <= 0:
        return "维持", y_price, "当前价格缺失或为0，跳过自动建议", "0%"

    # 价格差
    diff_pct = (c_price - y_price) / y_price * 100

    # 库存
    y_stock_num = extract_stock_num(y_stock)

    # 核心逻辑
    if y_stock_num is not None and y_stock_num <= 2:
        # 库存极低，强力收紧
        suggested = int(y_price * 1.15)
        max_price = int(y_price * (1 + CONFIG["price_change_limit_pct"]))
        suggested = min(suggested, max_price)
        action = "⚡急调"
        reason = f"库存仅剩{y_stock_num}间，建议收紧"

    elif diff_pct >= 10:
        # 竞品显著高于我们，可涨
        suggested = int(y_price + (c_price - y_price) * 0.6)
        max_price = int(y_price * (1 + CONFIG["price_change_limit_pct"]))
        suggested = min(suggested, max_price)
        action = "📈可涨"
        reason = f"竞品¥{c_price}，我们¥{y_price}，低{int(diff_pct)}%"

    elif diff_pct <= -10:
        # 竞品低于我们，建议跟进
        suggested = int(y_price + (c_price - y_price) * 0.6)
        min_price = int(y_price * (1 - CONFIG["price_change_limit_pct"]))
        suggested = max(suggested, min_price)
        action = "📉可降"
        reason = f"竞品¥{c_price}，我们¥{y_price}，高{int(abs(diff_pct))}%"

    else:
        action = "➡️维持"
        suggested = y_price
        reason = f"竞品¥{c_price}，我们¥{y_price}，差{int(abs(diff_pct))}%，价格合理"

    change_pct = (suggested - y_price) / y_price * 100
    change_str = f"+{change_pct:.0f}%" if change_pct > 0 else f"{change_pct:.0f}%"

    return action, suggested, reason, change_str


def generate_report(data, matches, source_file):
    """生成调价建议报告"""
    now = datetime.now().strftime("%Y年%m月%d日 %H:%M")

    lines = [
        f"# 📊 因为旅行民宿调价建议",
        f"**生成时间：** {now}",
        f"**数据来源：** {source_file}",
        f"",
        f"---",
        f"",
        f"## 🏠 竞品参考（{CONFIG['comp_name']}）",
        f"",
    ]

    comp_data = next((c for c in data["competitors"] if CONFIG['comp_name'] in c["name"]), None)
    if comp_data:
        for r in comp_data["rooms"]:
            price = r.get('price_low') or r.get('price', '?')
            orig = f"(原价¥{r.get('original_price') or r.get('price_high', price)})" if r.get('original_price') != price else ""
            bed = f" [{r.get('bed_type', '')}]" if r.get('bed_type') else ""
            area = f" {r.get('area', '')}平" if r.get('area') else ""
            lines.append(f"- **{r['name']}**：¥{price}{orig}{bed}{area} | {r.get('stock', '有房')}")

    lines.append(f"")
    lines.append(f"## 🏡 因为旅行·IWAY艺墅 调价建议")
    lines.append(f"")

    yinwei_data = next((c for c in data["competitors"] if CONFIG['yinwei_name'] in c["name"]), None)

    for m in matches:
        y = m["yinwei"]
        action, suggested, reason, change = calc_adjustment(
            y.get('price_low') or y.get('price', 0),
            y.get('original_price') or y.get('price_high', 0),
            m["comp_price"],
            m.get("comp_original"),
            y.get("stock", "")
        )

        emoji = "🔥" if "急" in action else "📈" if "涨" in action else "📉" if "降" in action else "➡️"

        lines.append(f"### {y['name']}")
        lines.append(f"- 当前售价：**¥{y.get('price_low') or y.get('price', '?')}**")

        y_price = y.get('price_low') or y.get('price', 0)
        if y.get('original_price') and y['original_price'] != y_price:
            lines.append(f"- 挂牌价：¥{y['original_price']}")

        if m["comp"]:
            comp_name = m["comp"]["name"]
            lines.append(f"- 对标：{comp_name}（¥{m['comp_price']}）")

        lines.append(f"- 库存：{y.get('stock', '有房')}")
        lines.append(f"")
        lines.append(f"{emoji} **{action}** → 建议 **¥{suggested}**（{change}）")
        lines.append(f"- {reason}")
        lines.append(f"")

    return "\n".join(lines)


def main():
    # 加载数据
    data, source_file = load_latest_data()

    # 分离竞品和自家数据
    comp_data = next((c for c in data["competitors"] if CONFIG['comp_name'] in c["name"]), None)
    yinwei_data = next((c for c in data["competitors"] if CONFIG['yinwei_name'] in c["name"]), None)

    if not comp_data or not yinwei_data:
        print("❌ 未找到竞品或自家数据")
        return

    # 匹配
    matches = match_rooms(comp_data["rooms"], yinwei_data["rooms"])

    # 生成报告
    report = generate_report(data, matches, source_file)
    print(report)

    # 保存
    output_file = PRICING_DIR / f"recommendation_{datetime.now().strftime('%Y%m%d_%H%M')}.md"

    with output_file.open('w', encoding='utf-8') as f:
        f.write(report)

    print(f"\n✅ 报告已保存: {output_file}")


if __name__ == "__main__":
    main()
