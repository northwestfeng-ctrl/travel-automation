#!/usr/bin/env python3
"""
飞书推送 - 调价建议推送
使用飞书开放 API (App ID + App Secret)
"""
import argparse
import os
import sys
import json
from pathlib import Path
from datetime import datetime

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PRICING_DIR = os.path.join(SCRIPT_DIR, "pricing")
if PRICING_DIR not in sys.path:
    sys.path.insert(0, PRICING_DIR)

from recommendation_to_execution_plan import parse_recommendation_markdown
from feishu_client import USER_OPEN_ID, get_tenant_token, send_text_message
from execute_saved_plan import compute_plan_digest


def find_latest_recommendation():
    """找到最新的调价建议文件"""
    files = [f for f in os.listdir(PRICING_DIR) if f.startswith('recommendation_') and f.endswith('.md')]
    if not files:
        return None
    files.sort(reverse=True)
    return os.path.join(PRICING_DIR, files[0])


def find_latest_plan(min_mtime=None):
    """找到最新的 dry-run 执行计划文件"""
    artifacts_dir = os.path.join(PRICING_DIR, "artifacts")
    if not os.path.isdir(artifacts_dir):
        return None

    files = [
        f for f in os.listdir(artifacts_dir)
        if f.startswith("ebooking_execution_plan_") and f.endswith(".json")
    ]
    if not files:
        return None

    files.sort(reverse=True)
    for filename in files:
        path = os.path.join(artifacts_dir, filename)
        if min_mtime is not None and os.path.getmtime(path) < min_mtime:
            continue
        return path
    return None


def load_plan(path):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def parse_args():
    parser = argparse.ArgumentParser(description="Send the latest travel recommendation summary to Feishu.")
    parser.add_argument("--recommendation-file", help="Recommendation markdown path. Defaults to the latest one.")
    parser.add_argument("--plan-file", help="Execution plan JSON path. Defaults to the latest plan not older than the recommendation.")
    parser.add_argument("--dry-run", action="store_true", help="Print the message without sending it.")
    return parser.parse_args()


def format_recommendation_lines(items):
    lines = [f"建议房型：{len(items)}组"]
    for item in items:
        lines.append(
            f"- {item['sourceRoomName']}：¥{item['currentPublicPrice']} → ¥{item['suggestedPublicPrice']} "
            f"（{item['action']}，{item['reason'] or '待人工确认'}）"
        )
    return lines


def format_plan_group_line(group):
    operations = group.get("operations", [])
    targets = sorted({int(operation["targetSalePrice"]) for operation in operations})
    currents = sorted({int(operation["currentEbookingSalePrice"]) for operation in operations})

    if len(currents) == 1 and len(targets) == 1:
        price_summary = f"¥{currents[0]} → ¥{targets[0]}"
    else:
        price_summary = (
            f"当前¥{currents[0]}-{currents[-1]} → 目标¥{targets[0]}-{targets[-1]}"
            if currents and targets
            else "目标价待确认"
        )

    return (
        f"- {group['sourceRoomName']}：{len(operations)}个子产品，{price_summary} "
        f"（映射置信度：{group['mappingConfidence']}）"
    )


def format_plan_lines(plan):
    groups = plan.get("planGroups", [])
    operation_count = sum(len(group.get("operations", [])) for group in groups)
    date_range = plan.get("dateRange", {})

    lines = [
        "ebooking dry-run 计划：",
        f"- 日期：{date_range.get('startDate')} 至 {date_range.get('endDate')}",
        f"- 映射房型：{len(groups)}组，子产品：{operation_count}个，未映射：{len(plan.get('unmappedRoomProductIds', []))}个",
    ]
    for group in groups:
        lines.append(format_plan_group_line(group))
    return lines


def build_message(recommendation_file, plan_file=None):
    items = parse_recommendation_markdown(Path(recommendation_file))
    lines = [
        "因为旅行民宿调价建议",
        f"生成时间：{datetime.now().strftime('%Y-%m-%d %H:%M')}",
        f"建议文件：{os.path.basename(recommendation_file)}",
        "",
    ]
    lines.extend(format_recommendation_lines(items))

    if plan_file:
        plan = load_plan(plan_file)
        lines.append("")
        lines.extend(format_plan_lines(plan))
        lines.append("")
        lines.append("说明：当前仅生成 dry-run 计划，尚未自动改价。")
    else:
        lines.append("")
        lines.append("说明：当前未附带当日 ebooking dry-run 执行计划。")

    lines.append("")
    lines.append("审批口令：回复“确认改价”批准执行；回复“取消改价”拒绝执行。")

    return "\n".join(lines)


def save_dispatch_record(recommendation_file, plan_file, result, content):
    artifacts_dir = os.path.join(PRICING_DIR, "artifacts")
    os.makedirs(artifacts_dir, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    path = os.path.join(artifacts_dir, f"feishu_approval_request_{stamp}.json")
    payload = {
        "generatedAt": datetime.now().isoformat(),
        "status": "pending",
        "approvalKeywords": ["确认改价", "批准改价", "执行改价", "同意改价"],
        "rejectKeywords": ["取消改价", "拒绝改价", "暂不改价", "不要改价"],
        "recommendationFile": recommendation_file,
        "planFile": plan_file,
        "planDigest": compute_plan_digest(Path(plan_file)) if plan_file else None,
        "feishuMessage": result.get("data", {}),
        "receiverOpenId": USER_OPEN_ID,
        "user_open_id": USER_OPEN_ID,
        "approverOpenId": USER_OPEN_ID,
        "content": content,
    }
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    return path


def main():
    args = parse_args()

    # 找到最新建议
    latest_file = args.recommendation_file or find_latest_recommendation()
    if not latest_file:
        print("❌ 未找到调价建议文件")
        sys.exit(1)

    latest_file = os.path.abspath(latest_file)
    plan_file = args.plan_file or find_latest_plan(min_mtime=os.path.getmtime(latest_file))
    full_msg = build_message(latest_file, plan_file)

    if args.dry_run:
        print(full_msg)
        return

    # 获取 token 并发送
    token = get_tenant_token()
    result = send_text_message(token, USER_OPEN_ID, full_msg)
    dispatch_path = save_dispatch_record(latest_file, plan_file, result, full_msg)
    print(f"✅ 飞书推送成功")
    print(f"   消息ID: {result.get('data', {}).get('message_id')}")
    print(f"   审批记录: {dispatch_path}")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"❌ 推送失败: {e}")
        sys.exit(1)
