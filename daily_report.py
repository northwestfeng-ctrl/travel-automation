#!/usr/bin/env python3
"""
每日18:00汇报脚本
统计24小时内的自动回复情况并发送报告
"""
import os
import json
from datetime import datetime

from runtime_config import load_project_env, require_env

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))

load_project_env()
FEISHU_APP_ID = require_env("FEISHU_APP_ID")
FEISHU_APP_SECRET = require_env("FEISHU_APP_SECRET")
FEISHU_USER_ID = require_env("FEISHU_USER_OPEN_ID")


def get_accounts():
    configured = os.environ.get("TRAVEL_AUTO_REPLY_ACCOUNTS", "").strip()
    if configured:
        return [item.strip() for item in configured.split(",") if item.strip()]

    discovered = []
    for name in sorted(os.listdir(SCRIPT_DIR)):
        if name.startswith("data_") and os.path.isdir(os.path.join(SCRIPT_DIR, name)):
            discovered.append(name[len("data_"):])
    return discovered


def account_paths(account_id: str) -> dict[str, str]:
    data_dir = os.path.join(SCRIPT_DIR, f"data_{account_id}")
    return {
        "data_dir": data_dir,
        "state": os.path.join(data_dir, "reply_state.json"),
        "health": os.path.join(data_dir, "health_data.json"),
        "alert": os.path.join(data_dir, "health_alert.json"),
        "log": os.path.join(SCRIPT_DIR, "logs", f"auto_reply_{account_id}_{datetime.now().strftime('%Y%m%d')}.log"),
    }

def get_feishu_token():
    """获取飞书tenant_access_token"""
    import urllib.request
    import urllib.parse

    url = "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal"
    data = json.dumps({"app_id": FEISHU_APP_ID, "app_secret": FEISHU_APP_SECRET}).encode()

    req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            result = json.loads(resp.read())
            if result.get("code") == 0:
                return result.get("tenant_access_token")
    except Exception as e:
        print(f"获取token失败: {e}")
    return None

def send_feishu_message(token, user_id, content):
    """发送飞书消息"""
    import urllib.request

    url = "https://open.feishu.cn/open-apis/im/v1/messages?receive_id_type=open_id"
    data = {
        "receive_id": user_id,
        "msg_type": "text",
        "content": json.dumps({"text": content})
    }

    req = urllib.request.Request(
        url,
        data=json.dumps(data).encode(),
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {token}"
        },
        method="POST"
    )

    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            result = json.loads(resp.read())
            return result.get("code") == 0
    except Exception as e:
        print(f"发送失败: {e}")
        return False

def load_json(path, default):
    if os.path.exists(path):
        with open(path, 'r') as f:
            return json.load(f)
    return default

def get_today_stats():
    """获取今日统计"""
    # 今日00:00到现在
    today_start = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)

    details = []
    per_account = []
    total_log_replies = 0
    total_log_errors = 0

    for account_id in get_accounts():
        paths = account_paths(account_id)
        state = load_json(paths["state"], {"replied": []})
        replied = state.get("replied", [])
        today_replies = [
            {**item, "accountId": account_id}
            for item in replied
            if datetime.fromisoformat(item['time']) >= today_start
        ]

        log_replies = 0
        log_errors = 0
        if os.path.exists(paths["log"]):
            with open(paths["log"], 'r') as f:
                content = f.read()
                log_replies = content.count('✅ 已发送')
                log_errors = content.count('❌') + content.count('失败') + content.count('出错')

        details.extend(today_replies)
        total_log_replies += log_replies
        total_log_errors += log_errors
        per_account.append(
            {
                "accountId": account_id,
                "replyCount": len(today_replies),
                "logReplies": log_replies,
                "logErrors": log_errors,
            }
        )

    details.sort(key=lambda item: item["time"])
    return {
        "count": len(details),
        "details": details,
        "log_replies": total_log_replies,
        "log_errors": total_log_errors,
        "per_account": per_account,
    }

def check_health():
    """检查自动回复健康状态"""
    accounts = []
    errors = []

    for account_id in get_accounts():
        paths = account_paths(account_id)
        health = load_json(paths["health"], {})
        status = health.get("status", "unknown")
        last_run = health.get("last_ok_time") or health.get("last_error_time")

        if os.path.exists(paths["log"]):
            with open(paths["log"], 'r') as f:
                lines = f.readlines()
                recent = lines[-20:] if len(lines) > 20 else lines
                for line in recent:
                    if '❌' in line or '出错' in line or '失败' in line:
                        errors.append(f"[{account_id}] {line.strip()}")

        accounts.append(
            {
                "accountId": account_id,
                "status": status,
                "lastRun": last_run,
                "consecutiveFailures": health.get("consecutive_failures", 0),
            }
        )

    overall_status = "running" if accounts and all(item["status"] == "running" for item in accounts) else "degraded"
    return {
        "status": overall_status,
        "accounts": accounts,
        "errors": errors[-5:],
    }

def main():
    stats = get_today_stats()
    health = check_health()

    # 格式化报告
    lines = [
        "🦞 自动回复日报",
        f"📅 {datetime.now().strftime('%Y年%m月%d日 %H:%M')}",
        "",
        f"📊 今日数据：",
        f"  • 自动回复：{stats['count']} 条",
        f"  • 处理会话：{stats['log_replies']} 次",
        f"  • 运行异常：{stats['log_errors']} 次",
        "",
        f"🤖 系统状态：",
        f"  • {health['status']}",
    ]

    if stats["per_account"]:
        lines.append("")
        lines.append("🏨 分账号统计：")
        for item in stats["per_account"]:
            lines.append(
                f"  • {item['accountId']}：回复 {item['replyCount']} 条，会话 {item['logReplies']} 次，异常 {item['logErrors']} 次"
            )

    if health["accounts"]:
        lines.append("")
        lines.append("🔎 分账号健康：")
        for item in health["accounts"]:
            lines.append(
                f"  • {item['accountId']}：{item['status']}，连续失败 {item['consecutiveFailures']} 次"
            )

    if health['errors']:
        lines.append("")
        lines.append("⚠️ 最近错误：")
        for err in health['errors'][:3]:
            lines.append(f"  {err}")

    # 详细回复记录
    if stats['details']:
        lines.append("")
        lines.append("📝 今日回复详情：")
        for r in stats['details'][-5:]:  # 最近5条
            t = datetime.fromisoformat(r['time']).strftime('%H:%M')
            lines.append(f"  [{t}] [{r['accountId']}] {r['guest']}：{r['reply'][:30]}...")

    lines.append("")
    lines.append("— 因为旅行·携程客服自动回复")

    report = "\n".join(lines)
    print(report)

    # 发送飞书
    token = get_feishu_token()
    if token:
        ok = send_feishu_message(token, FEISHU_USER_ID, report)
        if ok:
            print("\n✅ 飞书报告已发送")
        else:
            print("\n⚠️ 飞书发送失败")
    else:
        print("\n⚠️ 无法获取飞书token，报告仅显示在上方")

if __name__ == "__main__":
    main()
