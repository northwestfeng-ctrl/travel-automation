#!/usr/bin/env python3
"""
携程客服自动回复 - 多账号并行版
支持多账号隔离：python3 auto_reply.py [account_id]
策略:先API查未读数,有未读才启动浏览器处理
防重复:每个会话30分钟内只回复一次
"""
import os
import sys
import json
import time
import requests
import re
import base64
import tempfile
import shutil
from urllib.parse import parse_qs, unquote, urlparse
from datetime import datetime, timedelta
from playwright.sync_api import sync_playwright

from runtime_config import load_project_env, require_env
from reply_logic import (
    build_service_logic_reply,
    format_corpus_examples,
    is_allowed_corpus_pair,
    sanitize_generated_reply,
)
from session_extract import EXTRACT_SESSIONS_JS_CONTENT, write_extract_sessions_file

load_project_env()


SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
ACCOUNT_CONFIG_FILE = os.path.join(SCRIPT_DIR, "auto_reply_accounts.local.json")
HOTEL_INFO_DIR = os.path.join(SCRIPT_DIR, "hotel_info")
IM_ENTRY_URL = "https://ebooking.ctrip.com/im/index?module=replyCustomer&groupId=&orderId=&pageId=10650010602"
PUBLIC_CTRIP_HOTEL_URL = "https://m.ctrip.com/webapp/hotels/detail?hotelid={hotel_id}"


def load_account_configs():
    if not os.path.exists(ACCOUNT_CONFIG_FILE):
        raise FileNotFoundError(
            f"missing account config file: {ACCOUNT_CONFIG_FILE}. "
            "Create it from auto_reply_accounts.example.json or the existing local session export."
        )
    with open(ACCOUNT_CONFIG_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


# ============ 账号配置 ============
ACCOUNT_ID = sys.argv[1] if len(sys.argv) > 1 else "hotel_1164390341"
ACCOUNT_CONFIGS = load_account_configs()

cfg = ACCOUNT_CONFIGS.get(ACCOUNT_ID)
if cfg is None:
    print(f"❌ 未知账号: {ACCOUNT_ID}，可用: {list(ACCOUNT_CONFIGS.keys())}")
    sys.exit(1)

# ============ 路径配置（账号隔离）============
DATA_DIR = os.path.join(SCRIPT_DIR, f"data_{ACCOUNT_ID}")
os.makedirs(DATA_DIR, exist_ok=True)
STATE_FILE = os.path.join(DATA_DIR, "reply_state.json")
LOG_FILE = os.path.join(SCRIPT_DIR, "logs", f"auto_reply_{ACCOUNT_ID}_{datetime.now().strftime('%Y%m%d')}.log")
CORPUS_FILE = os.path.join(DATA_DIR, "colleague_corpus.jsonl")
DEFAULT_CORPUS_FILE = os.path.join(SCRIPT_DIR, "colleague_corpus.jsonl")
HEALTH_FILE = os.path.join(DATA_DIR, "health_data.json")
ALERT_FILE = os.path.join(DATA_DIR, "health_alert.json")
BROWSER_USER_DATA = f"/tmp/playwright_ctrip_{ACCOUNT_ID}"

# ============ 常量 ============
CRON_ALERT_THRESHOLD = 3
FEISHU_APP_ID = require_env("FEISHU_APP_ID")
FEISHU_APP_SECRET = require_env("FEISHU_APP_SECRET")
FEISHU_USER_ID = require_env("FEISHU_USER_OPEN_ID")
REPLY_COOLDOWN = 30 * 60
DRY_RUN = cfg.get("DRY_RUN", False)  # 支持账号级Override

# ============ 动态Cookie生成（账号级）============
def build_cookies_str(cfg):
    """从账号配置构建Cookie字符串（用于API请求头）"""
    pairs = [
        ("hotelhst", cfg["hotelhst"]),
        ("w_lid", cfg["w_lid"]),
        ("w_tuid", cfg["w_tuid"]),
        ("usersign", cfg["usersign"]),
        ("cticket", cfg["cticket"]),
        ("GUID", cfg["GUID"]),
        ("login_uid", cfg["login_uid"]),
        ("_bfa", cfg.get("_bfa", "")),
        ("usertoken", cfg["usertoken"]),
        ("Union", cfg["Union"]),
    ]
    return "; ".join(f"{k}={v}" for k, v in pairs if v)

def build_cookies_raw(cfg):
    """从账号配置构建Playwright可用Cookie列表"""
    cookies = [
        {"domain": ".ctrip.com", "httpOnly": False, "name": "GUID", "path": "/", "sameSite": "Lax", "secure": False, "value": cfg["GUID"]},
        {"domain": ".ctrip.com", "httpOnly": False, "name": "login_uid", "path": "/", "sameSite": "Strict", "secure": True, "value": cfg["login_uid"]},
        {"domain": ".ctrip.com", "httpOnly": True, "name": "cticket", "path": "/", "sameSite": "Strict", "secure": True, "value": cfg["cticket"]},
        {"domain": ".ctrip.com", "httpOnly": True, "name": "usersign", "path": "/", "sameSite": "Lax", "secure": False, "value": cfg["usersign"]},
        {"domain": ".ctrip.com", "httpOnly": False, "name": "usertoken", "path": "/", "sameSite": "Lax", "secure": False, "value": cfg["usertoken"]},
        {"domain": ".ctrip.com", "httpOnly": False, "name": "Union", "path": "/", "sameSite": "Lax", "secure": False, "value": cfg["Union"]},
        {"domain": ".ctrip.com", "httpOnly": False, "name": "_bfa", "path": "/", "sameSite": "Lax", "secure": False, "value": cfg.get("_bfa", "")},
        {"domain": "ebooking.ctrip.com", "httpOnly": False, "name": "hotelhst", "path": "/", "sameSite": "Lax", "secure": False, "value": cfg["hotelhst"]},
        {"domain": "ebooking.ctrip.com", "httpOnly": False, "name": "w_lid", "path": "/", "sameSite": "Lax", "secure": False, "value": cfg["w_lid"]},
        {"domain": "ebooking.ctrip.com", "httpOnly": False, "name": "w_tuid", "path": "/", "sameSite": "Lax", "secure": False, "value": cfg["w_tuid"]},
        {"domain": "ebooking.ctrip.com", "httpOnly": False, "name": "CurrentLanguage", "path": "/", "sameSite": "Lax", "secure": False, "value": "SimpChinese"},
        {"domain": "ebooking.ctrip.com", "httpOnly": False, "name": "EBK_CurrentLocale", "path": "/", "sameSite": "Lax", "secure": False, "value": "zh-CN"},
    ]
    return [c for c in cookies if c["value"]]

COOKIES_STR = build_cookies_str(cfg)
HEADERS = {
    "Cookie": COOKIES_STR,
    "GUID": cfg["GUID"],
    "login_uid": cfg["login_uid"],
    "Content-Type": "application/json"
}
COOKIES_RAW = build_cookies_raw(cfg)

def log(msg):
    os.makedirs(os.path.join(SCRIPT_DIR, "logs"), exist_ok=True)
    ts = datetime.now().strftime('%H:%M:%S')
    line = f"[{ts}] {msg}"
    print(line)
    with open(LOG_FILE, 'a') as f:
        f.write(line + "\n")

def load_state():
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE, 'r') as f:
                data = json.load(f)
        except (json.JSONDecodeError, IOError):
            # 文件损坏,备份并重建
            shutil.copy(STATE_FILE, STATE_FILE + ".bak")
            log("⚠️ state文件损坏,已备份并重建")
            data = {"replied": [], "last_check": None, "recent_replies": {}}
    else:
        data = {"replied": [], "last_check": None, "recent_replies": {}}

    # 清理超过30分钟的旧记录
    now = datetime.now()
    cutoff = now - timedelta(seconds=REPLY_COOLDOWN)
    cleaned = {}
    for k, v in data.get("recent_replies", {}).items():
        try:
            if datetime.fromisoformat(v) > cutoff:
                cleaned[k] = v
        except (ValueError, TypeError):
            pass  # 无效的时间格式,跳过
    data["recent_replies"] = cleaned

    replied_cutoff = now - timedelta(days=7)
    replied_records = []
    for item in data.get("replied", []):
        try:
            item_time = datetime.fromisoformat(item.get("time", ""))
        except (ValueError, TypeError):
            continue
        if item_time > replied_cutoff:
            replied_records.append(item)
    data["replied"] = replied_records

    return data

def save_state(state):
    # 原子写入:先写临时文件,再rename
    dir_name = os.path.dirname(STATE_FILE) or '.'
    fd, tmp_path = tempfile.mkstemp(dir=dir_name, suffix='.json')
    try:
        with os.fdopen(fd, 'w') as f:
            json.dump(state, f, ensure_ascii=False, indent=2)
        shutil.move(tmp_path, STATE_FILE)
    except Exception:
        # 写入失败,删除临时文件
        try:
            os.unlink(tmp_path)
        except:
            pass


def write_local_warning_alert(kind, message, details=None):
    os.makedirs(DATA_DIR, exist_ok=True)
    payload = {
        "type": kind,
        "time": datetime.now().isoformat(),
        "message": message,
        "details": details or {},
    }
    warn_path = os.path.join(DATA_DIR, f"{kind}_warn.json")
    with open(warn_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    log(f"⚠️ 已写入本地告警: {warn_path}")

def resolve_hotel_info_file():
    candidates = []

    custom_path = cfg.get("hotel_info_file")
    if custom_path:
        if os.path.isabs(custom_path):
            candidates.append(custom_path)
        else:
            candidates.append(os.path.join(SCRIPT_DIR, custom_path))

    for identifier in [
        str(cfg.get("hotelhst", "")).strip(),
        str(cfg.get("ctrip_hotel_id", "")).strip(),
        ACCOUNT_ID,
    ]:
        if identifier:
            candidates.append(os.path.join(HOTEL_INFO_DIR, f"{identifier}.md"))

    seen = set()
    for path in candidates:
        if not path or path in seen:
            continue
        seen.add(path)
        if os.path.exists(path):
            return path
    return None


def _extract_balanced_fragment(text, start_index):
    level = 0
    for idx, ch in enumerate(text[start_index:], start_index):
        if ch == "{":
            level += 1
        elif ch == "}":
            level -= 1
            if level == 0:
                return text[start_index : idx + 1]
    return None


def _fully_unquote(value, rounds=3):
    current = value or ""
    for _ in range(rounds):
        decoded = unquote(current)
        if decoded == current:
            break
        current = decoded
    return current


def _extract_phone_candidate(*texts):
    patterns = [
        r'hotelPhone":"([^"]+)"',
        r"hotelPhone=([^&]+)",
        r"电话[:：]([+0-9\\-\\s]{7,})",
    ]
    for raw_text in texts:
        if not raw_text:
            continue
        text = _fully_unquote(raw_text)
        for pattern in patterns:
            match = re.search(pattern, text)
            if not match:
                continue
            candidate = match.group(1).strip()
            candidate = candidate.replace("%2B", "+")
            candidate = re.sub(r"\s+", "", candidate)
            if candidate:
                return candidate
    return None


def fetch_runtime_hotel_info():
    try:
        resp = requests.get(
            IM_ENTRY_URL,
            headers={
                "Cookie": COOKIES_STR,
                "User-Agent": "Mozilla/5.0",
            },
            timeout=15,
        )
        match = re.search(
            r"window\.HEAppInfo\s*=\s*\{.*?getHotelInfo:\s*function\s*\(\)\s*\{\s*return\s*(\{.*?\})\s*;\s*\}",
            resp.text,
            re.S,
        )
        if not match:
            return {}
        return json.loads(match.group(1))
    except Exception as exc:
        log(f"⚠️ 自动读取账号酒店信息失败: {exc}")
        return {}


def fetch_public_ctrip_hotel_detail(ctrip_hotel_id):
    if not ctrip_hotel_id:
        return {}

    try:
        resp = requests.get(
            PUBLIC_CTRIP_HOTEL_URL.format(hotel_id=ctrip_hotel_id),
            headers={"User-Agent": "Mozilla/5.0"},
            timeout=20,
        )
        needle = '\\"detailResponse\\":'
        start_marker = resp.text.find(needle)
        if start_marker == -1:
            return {}

        fragment_start = resp.text.find("{", start_marker + len(needle))
        if fragment_start == -1:
            return {}

        fragment = _extract_balanced_fragment(resp.text, fragment_start)
        if not fragment:
            return {}

        json_text = fragment.encode("utf-8").decode("unicode_escape").encode("latin1").decode("utf-8")
        detail_response = json.loads(json_text)
        data = detail_response.get("data", {})

        hotel_base_info = data.get("hotelBaseInfo", {})
        hotel_position_info = data.get("hotelPositionInfo", {})
        facility_list = data.get("hotelFacilityBelt", {}).get("facilityList", [])
        facility_descs = [
            item.get("facilityDesc", "").strip()
            for item in facility_list
            if item.get("facilityDesc", "").strip()
        ]

        phone = _extract_phone_candidate(
            hotel_position_info.get("url", ""),
            resp.text,
        )
        return {
            "name": hotel_base_info.get("nameInfo", {}).get("name"),
            "city_name": hotel_base_info.get("cityName"),
            "address": hotel_position_info.get("address"),
            "traffic_desc": hotel_position_info.get("trafficInfo", {}).get("trafficDesc"),
            "phone": phone,
            "facility_descs": facility_descs,
        }
    except Exception as exc:
        log(f"⚠️ 自动读取携程酒店详情失败(hotelId={ctrip_hotel_id}): {exc}")
        return {}


def build_dynamic_hotel_profile(runtime_info=None):
    runtime_info = runtime_info or fetch_runtime_hotel_info()
    ctrip_hotel_id = ""
    public_detail = {}
    candidate_ids = [
        runtime_info.get("masterHotelId"),
        cfg.get("ctrip_hotel_id"),
        cfg.get("public_ctrip_hotel_id"),
        cfg.get("hotelhst"),
    ]
    seen_candidate_ids = set()
    for candidate in candidate_ids:
        candidate_str = str(candidate or "").strip()
        if not candidate_str or candidate_str in seen_candidate_ids:
            continue
        seen_candidate_ids.add(candidate_str)
        detail = fetch_public_ctrip_hotel_detail(candidate_str)
        if any([detail.get("name"), detail.get("address"), detail.get("facility_descs")]):
            ctrip_hotel_id = candidate_str
            public_detail = detail
            break

    name = public_detail.get("name") or runtime_info.get("hotelName") or runtime_info.get("hotelCName")
    address = public_detail.get("address")
    phone = public_detail.get("phone")
    city_name = public_detail.get("city_name") or runtime_info.get("cityName")
    traffic_desc = public_detail.get("traffic_desc")
    facility_descs = public_detail.get("facility_descs", [])

    if not any([name, address, phone, city_name, facility_descs]):
        return None

    lines = ["# 自动读取酒店信息", "", "## 基本信息"]
    if name:
        lines.append(f"- **名称**：{name}")
    if address:
        lines.append(f"- **地址**：{address}")
    if phone:
        lines.append(f"- **电话**：{phone}（仅内部参考，禁止在客服回复中直接发手机号）")
    if cfg.get("hotelhst"):
        lines.append(f"- **eBooking酒店ID**：{cfg['hotelhst']}")
    if ctrip_hotel_id:
        lines.append(f"- **携程酒店ID**：{ctrip_hotel_id}")
    if city_name:
        lines.append(f"- **城市**：{city_name}")

    if traffic_desc:
        lines.extend(["", "## 交通", f"- {traffic_desc}"])

    if facility_descs:
        lines.extend(["", "## 设施标签"])
        for desc in facility_descs[:12]:
            lines.append(f"- {desc}")

    text = "\n".join(lines)
    return {
        "text": text,
        "facts": parse_hotel_info_facts(text),
        "path": f"auto://ctrip/{ctrip_hotel_id or cfg.get('hotelhst', 'unknown')}",
    }


def extract_runtime_hotel_info_from_page(page):
    try:
        hotel_info = page.evaluate(
            """() => {
                try {
                    if (window.HEAppInfo && typeof window.HEAppInfo.getHotelInfo === 'function') {
                        return window.HEAppInfo.getHotelInfo();
                    }
                } catch (error) {}
                return null;
            }"""
        )
        return hotel_info or {}
    except Exception as exc:
        log(f"⚠️ 页面内读取酒店信息失败: {exc}")
        return {}


def parse_hotel_info_facts(text):
    facts = {
        "name": None,
        "address": None,
        "phone": None,
        "checkin_time": None,
        "checkout_time": None,
        "breakfast_available": None,
        "breakfast_hours": None,
        "breakfast_price": None,
        "pets_allowed": None,
        "pets_text": None,
        "parking_text": None,
        "wifi_text": None,
        "pool_text": None,
        "restaurant_text": None,
        "pickup_text": None,
        "aircon_text": None,
    }

    raw_lines = [line.strip() for line in text.splitlines() if line.strip()]
    lines = [line.replace("**", "") for line in raw_lines]
    for line in lines:
        if line.startswith("## "):
            continue
        if line.startswith("- 名称："):
            facts["name"] = line.split("：", 1)[1].strip()
        elif line.startswith("- 地址："):
            facts["address"] = line.split("：", 1)[1].strip()
        elif line.startswith("- 电话："):
            facts["phone"] = line.split("：", 1)[1].strip()
        elif line.startswith("- 入住时间："):
            facts["checkin_time"] = line.split("：", 1)[1].strip()
        elif line.startswith("- 退房时间："):
            facts["checkout_time"] = line.split("：", 1)[1].strip()
        elif line.startswith("- 营业时间："):
            facts["breakfast_hours"] = line.split("：", 1)[1].strip()
        elif line.startswith("- 费用："):
            facts["breakfast_price"] = line.split("：", 1)[1].strip()

    lowered = text.lower()
    if "早餐" in text:
        facts["breakfast_available"] = True
    if "酒店不提供早餐" in text or "不提供早餐" in text:
        facts["breakfast_available"] = False

    if "允许携带宠物" in text:
        facts["pets_allowed"] = True
    elif "宠物友好" in text:
        facts["pets_allowed"] = True
    elif "不可携带宠物" in text or "不允许携带宠物" in text:
        facts["pets_allowed"] = False

    if "宠物" in text:
        pet_lines = [line for line in lines if line.startswith("- ") and "宠物" in line]
        if pet_lines:
            facts["pets_text"] = "；".join(line.lstrip("- ").strip() for line in pet_lines[:2])

    facility_keywords = {
        "parking_text": ["停车"],
        "wifi_text": ["wifi", "wi-fi", "无线"],
        "pool_text": ["泳池", "游泳"],
        "restaurant_text": ["餐厅", "咖啡厅", "公共厨房"],
        "pickup_text": ["接站", "接机", "接送", "送站", "送机"],
        "aircon_text": ["空调"],
    }

    for fact_key, keywords in facility_keywords.items():
        matched_lines = []
        for line in lines:
            if re.match(r"-\s*[^：:]+[：:]", line):
                continue
            lowered_line = line.lower()
            if any(keyword in line or keyword in lowered_line for keyword in keywords):
                matched_lines.append(line.lstrip("- ").strip())
        if matched_lines:
            facts[fact_key] = "；".join(matched_lines[:2])

    return facts


def load_hotel_profile():
    path = resolve_hotel_info_file()
    if not path:
        log(f"⚠️ 未找到酒店资料文件(account={ACCOUNT_ID}, hotelhst={cfg.get('hotelhst')})，尝试自动读取酒店基础信息")
        dynamic_profile = build_dynamic_hotel_profile()
        if dynamic_profile:
            log(f"📘 已自动读取酒店资料: {dynamic_profile['path']}")
            return dynamic_profile
        return {"text": "", "facts": {}, "path": None}

    with open(path, 'r', encoding='utf-8') as f:
        text = f.read()
    log(f"📘 已加载酒店资料: {path}")
    return {
        "text": text,
        "facts": parse_hotel_info_facts(text),
        "path": path,
    }

def is_recently_replied(state, conv_key):
    """检查该会话是否在30分钟内已回复过"""
    recent = state.get("recent_replies", {})
    if conv_key in recent:
        last_reply = datetime.fromisoformat(recent[conv_key])
        if datetime.now() - last_reply < timedelta(seconds=REPLY_COOLDOWN):
            return True
    return False

def mark_replied(state, conv_key):
    """标记会话已回复"""
    if "recent_replies" not in state:
        state["recent_replies"] = {}
    state["recent_replies"][conv_key] = datetime.now().isoformat()

def check_unread_count():
    """轻量级API检查未读数"""
    try:
        resp = requests.post(
            "https://ebooking.ctrip.com/restapi/soa2/24521/imGetUnreadCount",
            headers=HEADERS,
            json={"hotelId": int(cfg["hotelhst"])},
            timeout=10
        )
        data = resp.json()
        if data.get("ResponseStatus", {}).get("Ack") == "Success":
            return data.get("totalUnreadCount", 0)
    except Exception as e:
        log(f"检查未读数失败: {e}")
    return None

def check_cookie_expiry(auth_verified=False):
    """检查Cookie是否即将过期,提前3天提醒"""
    import json as json_lib

    expiry_warn_file = os.path.join(DATA_DIR, "cookie_expiry_warned")

    def parse_expiry_from_cfg() -> datetime | None:
        explicit_iso = cfg.get("cookie_expires_at")
        if explicit_iso:
            try:
                return datetime.fromisoformat(explicit_iso)
            except ValueError:
                log(f"⚠️ cookie_expires_at 格式无效: {explicit_iso}")

        explicit_epoch = cfg.get("cookie_expires_epoch")
        if explicit_epoch not in (None, ""):
            try:
                epoch_value = float(explicit_epoch)
                if epoch_value > 10_000_000_000:
                    epoch_value /= 1000.0
                return datetime.fromtimestamp(epoch_value)
            except (TypeError, ValueError):
                log(f"⚠️ cookie_expires_epoch 格式无效: {explicit_epoch}")

        union_value = cfg.get("Union", "")
        match = re.search(r"Expires=(\d+)", union_value)
        if match:
            epoch_value = float(match.group(1))
            if epoch_value > 10_000_000_000:
                epoch_value /= 1000.0
            return datetime.fromtimestamp(epoch_value)

        usersign = cfg.get("usersign", "")
        if usersign:
            parts = usersign.split("_")
            candidates = []
            if len(parts) >= 3:
                candidates.append(parts[2])
            if len(parts) >= 2:
                candidates.append(parts[-1])
            for candidate in candidates:
                try:
                    decoded = base64.b64decode(candidate + "==").decode("utf-8")
                    data = json_lib.loads(decoded)
                except Exception:
                    continue
                exp = data.get("expires") or data.get("exp") or data.get("validTo") or data.get("valid_until")
                if isinstance(exp, (int, float)):
                    if exp > 10_000_000_000:
                        exp /= 1000.0
                    return datetime.fromtimestamp(exp)
        return None

    try:
        expiry_at = parse_expiry_from_cfg()
        if expiry_at is None:
            log("⚠️ 无法确定 Cookie 过期时间，跳过到期提醒；可在账号配置中补充 cookie_expires_at")
            return

        now = datetime.now()
        days_left = (expiry_at - now).days
        log(f"Cookie expires in {days_left} days")

        # 某些 cookie 字段中的过期时间可能已经陈旧，但当前登录态仍可用。
        # 这种情况下不应继续报警，并清理旧的误报标记。
        if auth_verified and days_left < 0:
            if os.path.exists(expiry_warn_file):
                os.remove(expiry_warn_file)
            log("✅ Cookie 过期元数据已陈旧，但当前登录态仍可用，跳过过期报警")
            return

        if days_left <= 3 and not os.path.exists(expiry_warn_file):
            send_cookie_expiry_alert(days_left)
            with open(expiry_warn_file, 'w') as f:
                f.write(f"{datetime.now().isoformat()}: {days_left} days")
            log("⚠️ Cookie即将过期,已发送提醒")
    except Exception:
        pass

def send_cookie_expiry_alert(days_left):
    """发送Cookie即将过期提醒"""
    token = None
    try:
        import urllib.request
        url = "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal"
        data = json.dumps({"app_id": FEISHU_APP_ID, "app_secret": FEISHU_APP_SECRET}).encode()
        req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            result = json.loads(resp.read())
            if result.get("code") == 0:
                token = result.get("tenant_access_token")
    except Exception as exc:
        write_local_warning_alert(
            "cookie_expiry_alert_failed",
            "飞书 Cookie 到期提醒发送失败",
            {"days_left": days_left, "error": str(exc)},
        )

    if not token:
        write_local_warning_alert(
            "cookie_expiry_alert_failed",
            "飞书 Cookie 到期提醒 token 为空",
            {"days_left": days_left},
        )
        return

    try:
        import urllib.request
        msg_url = "https://open.feishu.cn/open-apis/im/v1/messages?receive_id_type=open_id"
        content = f"🦞【提醒】携程Cookie即将过期\n\n⚠️ Cookie将在约 {days_left} 天后过期\n请提前准备好新的Cookie\n过期前请联系我更新"
        data = {
            "receive_id": FEISHU_USER_ID,
            "msg_type": "text",
            "content": json.dumps({"text": content})
        }
        req = urllib.request.Request(
            msg_url,
            data=json.dumps(data).encode(),
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {token}"
            },
            method="POST"
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            json.loads(resp.read())
    except:
        pass

MINIMAX_API_KEY = os.environ.get("MINIMAX_API_KEY", "").strip()
MINIMAX_BASE_URL = "https://api.minimax.chat"
MINIMAX_MODEL = "MiniMax-Text-01"

def load_corpus(limit=5):
    """加载历史语料,随机抽取limit条作为Few-shot示例"""
    corpus_path = CORPUS_FILE if os.path.exists(CORPUS_FILE) else DEFAULT_CORPUS_FILE
    if not os.path.exists(corpus_path):
        return []
    try:
        with open(corpus_path, 'r', encoding='utf-8') as f:
            lines = f.readlines()
        import random
        random.seed()
        corpus = []
        for line in lines:
            try:
                pair = json.loads(line.strip())
                if is_allowed_corpus_pair(pair):
                    corpus.append(pair)
            except (json.JSONDecodeError, ValueError):
                pass
        if not corpus:
            return []
        return random.sample(corpus, min(len(corpus), limit))
    except Exception:
        return []


def maybe_polish_reply_with_llm(base_reply, guest_name, guest_message, hotel_info, corpus_str):
    if not MINIMAX_API_KEY:
        return base_reply

    system_prompt = f"""你是携程酒店自动客服，需要在不改变业务含义的前提下，把基准回复润色得更自然。
民宿信息:
{hotel_info}

【历史回复风格参考】
{corpus_str}

要求:
- 你只能改写语气，不能改变基准回复的业务含义
- 你不能新增民宿信息、不能新增承诺、不能改变规则判断
- 你不能说自己离开、忙、稍后回复、回头回复
- 你不能提房源笔记、微信、加V、手机号
- 如果基准回复包含“请留下您的联系方式，管家第一时间致电”，必须完整保留这句话
- 输出30字以内；保留上述联系方式升级话术时可以超过30字
- 如果基准回复已经合适，原样输出
"""

    payload = {
        "model": MINIMAX_MODEL,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": f"客人({guest_name})发来消息:{guest_message}\n基准回复:{base_reply}"}
        ],
        "temperature": 0.2,
        "max_tokens": 80
    }

    try:
        resp = requests.post(
            f"{MINIMAX_BASE_URL}/v1/text/chatcompletion_v2",
            headers={
                "Authorization": f"Bearer {MINIMAX_API_KEY}",
                "Content-Type": "application/json"
            },
            json=payload,
            timeout=15
        )
        result = resp.json()
        if resp.status_code == 200 and result.get("choices"):
            candidate = result["choices"][0]["messages"][-1]["content"].strip()
            return sanitize_generated_reply(candidate, base_reply)
    except Exception as e:
        log(f"LLM润色失败: {e}")

    return base_reply


def generate_reply(guest_name, guest_message, hotel_profile):
    """统一酒店客服逻辑：规则决定业务回复，LLM仅做可控润色。"""
    corpus = load_corpus(limit=5)
    corpus_str = format_corpus_examples(corpus)
    hotel_info = hotel_profile.get("text", "")
    facts = hotel_profile.get("facts", {})
    base_reply = build_service_logic_reply(guest_message, facts)
    return maybe_polish_reply_with_llm(base_reply, guest_name, guest_message, hotel_info, corpus_str)


def click_conversation_by_exact_name(page, name):
    result = page.evaluate(
        """(targetName) => {
            const items = Array.from(document.querySelectorAll('[class*="groupInfo"]'));
            const item = items.find((node) => {
                const title = node.querySelector('[class*="groupTitle"]');
                return title && (title.textContent || '').trim() === targetName;
            });
            if (!item) return { ok: false, reason: 'not_found' };
            item.click();
            return { ok: true };
        }""",
        name,
    )
    if not result or not result.get("ok"):
        log(f"⚠️ 未找到精确会话标题，跳过发送: {name}")
        return False
    page.wait_for_timeout(3000)
    return True


def read_chat_input_text(inp):
    try:
        return inp.input_value(timeout=1000).strip()
    except Exception:
        try:
            text = inp.evaluate(
                """(node) => {
                    if (typeof node.value === 'string') return node.value;
                    return node.innerText || node.textContent || '';
                }"""
            )
            return (text or "").strip()
        except Exception:
            return ""


def send_reply_and_verify(page, reply):
    inp_locator = page.locator('[class*="imChatInput"], [class*="chatInput"], [class*="InputMain"], [contenteditable="true"]')
    if inp_locator.count() <= 0:
        log("⚠️ 未找到聊天输入框，跳过发送")
        return False

    inp = inp_locator.first
    inp.click()
    page.wait_for_timeout(300)
    page.keyboard.type(reply, delay=20)
    page.wait_for_timeout(500)
    page.keyboard.press('Enter')
    page.wait_for_timeout(1500)

    if not read_chat_input_text(inp):
        return True

    try:
        page.locator('text=发送').last.click(timeout=3000)
        page.wait_for_timeout(1000)
    except Exception:
        pass

    if read_chat_input_text(inp):
        log("⚠️ 消息发送后输入框仍有内容，判定发送失败")
        return False
    return True


def process_conversations_via_browser():
    """用Playwright处理会话(仅在有未读时调用)，支持浏览器崩溃重试"""
    from playwright.sync_api import sync_playwright
    from playwright_stealth import stealth as stealth_module
    import time

    state = load_state()
    hotel_profile = load_hotel_profile()
    headless = cfg.get("headless", True)
    max_retries = 3

    for attempt in range(1, max_retries + 1):
        page = None
        log(f"🚀 启动浏览器 (attempt {attempt}/{max_retries}, headless={headless}, account={ACCOUNT_ID})")
        try:
            with sync_playwright() as p:
                browser = p.chromium.launch(
                    headless=headless,
                    timeout=90000,
                    args=[
                        '--no-sandbox',
                        '--disable-dev-shm-usage',
                        '--disable-gpu',
                        '--hide-scrollbars',
                        '--mute-audio',
                        '--disable-blink-features=AutomationControlled',
                    ]
                )
                context = browser.new_context(
                    viewport={'width': 1366, 'height': 768},
                    user_agent='Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36'
                )
                # 拦截无关资源提速
                context.route('**/*', lambda route: route.abort()
                    if route.request.resource_type in ['image', 'media', 'font', 'stylesheet']
                    else route.continue_())
                context.add_cookies(COOKIES_RAW)
                page = context.new_page()
                page.set_default_timeout(60000)

                # 应用 stealth 反检测
                stealth_module.Stealth(
                    chrome_app=True,
                    chrome_csi=True,
                    chrome_load_times=True,
                    iframe_content_window=True,
                    media_codecs=True,
                    navigator_hardware_concurrency=True,
                    navigator_languages=True,
                    navigator_permissions=True,
                    navigator_platform=True,
                    navigator_user_agent=True,
                    navigator_vendor=True,
                    navigator_webdriver=True,
                    webgl_vendor=True,
                    hairline=True,
                ).apply_stealth_sync(page)

                page.goto(
                    'https://ebooking.ctrip.com/im/index?module=replyCustomer&groupId=&orderId=&pageId=10650010602',
                    timeout=60000
                )

                # 等待页面主体元素出现，不依赖 networkidle
                try:
                    page.wait_for_selector('body', state='visible', timeout=15000)
                except Exception:
                    pass
                page.wait_for_timeout(6000)

                # 关弹窗（携程的"我知道了"提示）
                try:
                    page.locator('text=知道了').first.click(timeout=5000)
                    page.wait_for_timeout(1000)
                except Exception:
                    pass

                # 点击"查看全部"展开完整列表
                try:
                    page.locator('text=查看全部').first.click(timeout=5000)
                    page.wait_for_timeout(2000)
                except Exception:
                    pass

                if not hotel_profile.get("text"):
                    runtime_info = extract_runtime_hotel_info_from_page(page)
                    dynamic_profile = build_dynamic_hotel_profile(runtime_info)
                    if dynamic_profile:
                        hotel_profile = dynamic_profile
                        log(f"📘 已从页面上下文补齐酒店资料: {hotel_profile['path']}")

                # 注入会话提取 JS
                page.add_script_tag(content=EXTRACT_SESSIONS_JS_CONTENT)
                page.wait_for_timeout(1500)
                conv_data = page.evaluate("window.__ctripExtractSessions()")

                log(f"发现 {len(conv_data)} 个会话")

                # 去重
                seen_names = set()
                all_convs = []
                for c in conv_data:
                    key = c.get('name', '')
                    if key and key not in seen_names:
                        seen_names.add(key)
                        all_convs.append(c)

                if not all_convs:
                    # 强制截图确认页面状态
                    try:
                        shot_path = f'/tmp/ctrip_empty_{datetime.now().strftime("%H%M%S")}.png'
                        page.screenshot(path=shot_path)
                        log(f"📸 页面快照(0会话): {shot_path}")
                        # 同时保存DOM备查
                        dom_path = f'/tmp/ctrip_empty_{datetime.now().strftime("%H%M%S")}.html'
                        with open(dom_path, 'w', encoding='utf-8') as f:
                            f.write(page.content())
                        log(f"📄 DOM已保存: {dom_path}")
                    except Exception:
                        pass
                    log("✅ 暂无会话")
                    save_state(state)
                    return 0

                unreplied = all_convs
                log(f"待处理会话: {', '.join(conv['name'] for conv in unreplied[:5])}")
                if len(unreplied) > 5:
                    log(f"其余待处理会话数: {len(unreplied) - 5}")
                sent_count = 0

                for conv in unreplied:
                    name = conv['name'] or '客人'
                    consult_sid = conv.get('consultSid', '')
                    conv_key = consult_sid if consult_sid else name

                    if is_recently_replied(state, conv_key):
                        log(f"⏭️ 跳过(刚回复过): {name}")
                        continue

                    log(f"处理: {name}")

                    guest_msg = conv.get('latestGuestMsg', '')
                    if not guest_msg:
                        lines = conv['text'].split('\n')
                        for line in lines:
                            if line and not any(c in line for c in ['未回复', '未读', '查看', '分钟', '小时', '昨天', '今天', '徐沐凡', '客人3485', '客人1785', '客人0553']):
                                guest_msg = line.strip()
                                break

                    if guest_msg and len(guest_msg) > 1:
                        reply = generate_reply(name, guest_msg, hotel_profile)
                        log(f"📝 客人最新消息: {guest_msg[:80]}")
                        log(f"📝 建议回复: {reply}")

                        if DRY_RUN:
                            log(f"⏸️ [DRY_RUN] 灰度观察期: 记录但不发送")
                            state.setdefault('replied', []).append({
                                "time": datetime.now().isoformat(),
                                "guest": name,
                                "original": guest_msg,
                                "reply": reply,
                                "dry_run": True
                            })
                            mark_replied(state, conv_key)
                            save_state(state)
                            continue
                        else:
                            try:
                                if not click_conversation_by_exact_name(page, name):
                                    continue
                                if not send_reply_and_verify(page, reply):
                                    log(f"⚠️ 消息发送失败，跳过标记: {name}")
                                    continue

                                mark_replied(state, conv_key)
                                state.setdefault('replied', []).append({
                                    "time": datetime.now().isoformat(),
                                    "guest": name,
                                    "original": guest_msg,
                                    "reply": reply
                                })
                                save_state(state)
                                sent_count += 1
                                log(f"✅ 已发送: {name}")
                            except Exception as e:
                                log(f"发送失败: {e}")

                save_state(state)
                return sent_count

        except Exception as e:
            ts = datetime.now().strftime("%H%M%S")
            log(f"❌ 浏览器异常 (attempt {attempt}/{max_retries}): {e}")
            # 捕获现场快照 + DOM源码
            if page:
                try:
                    shot_path = f'/tmp/ctrip_error_{ts}_a{attempt}.png'
                    page.screenshot(path=shot_path)
                    log(f"📸 快照: {shot_path}")
                except Exception:
                    pass
                try:
                    dom_path = f'/tmp/ctrip_dom_{ts}_a{attempt}.html'
                    with open(dom_path, 'w', encoding='utf-8') as f:
                        f.write(page.content())
                    log(f"📄 DOM: {dom_path}")
                except Exception:
                    pass
            if attempt < max_retries:
                log("🔄 清理残留进程并等待重试...")
                time.sleep(5)
                continue
            else:
                log("❌ 所有重试均失败")
                save_state(state)
                raise

def self_heal():
    """自检自愈：尝试修复已知问题，返回修复描述或None"""
    import subprocess
    fixed = []

    # 1. 检查 extract_sessions.js 是否存在
    js_path = os.path.join(SCRIPT_DIR, 'extract_sessions.js')
    if not os.path.exists(js_path):
        log("🔧 自愈: extract_sessions.js 丢失，重新创建...")
        write_extract_sessions_file(js_path)
        fixed.append("extract_sessions.js")
        log(f"✅ 自愈完成: {fixed}")

    # 2. 检查 playwright-stealth 是否可用（/usr/bin/python3）
    try:
        result = subprocess.run(
            ['/usr/bin/python3', '-c', 'import playwright_stealth'],
            capture_output=True, timeout=10
        )
        if result.returncode != 0:
            log("🔧 自愈: playwright-stealth 缺失，尝试安装...")
            subprocess.run(
                ['/usr/bin/python3', '-m', 'pip', 'install', 'playwright-stealth', '--quiet'],
                capture_output=True, timeout=60
            )
            try:
                subprocess.run(
                    ['/usr/bin/python3', '-c', 'import playwright_stealth'],
                    capture_output=True, timeout=10, check=True
                )
                fixed.append("playwright-stealth")
                log("✅ playwright-stealth 安装成功")
            except:
                log("⚠️ playwright-stealth 安装失败")
    except Exception as e:
        log(f"⚠️ 自愈检查失败: {e}")

    return fixed if fixed else None


def send_crash_alert(error_msg, consecutive):
    """发送崩溃报警到飞书"""
    token = None
    try:
        import urllib.request
        url = "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal"
        data = json.dumps({"app_id": FEISHU_APP_ID, "app_secret": FEISHU_APP_SECRET}).encode()
        req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            result = json.loads(resp.read())
            if result.get("code") == 0:
                token = result.get("tenant_access_token")
    except:
        pass

    if not token:
        write_local_warning_alert(
            "crash_alert_failed",
            "飞书崩溃报警获取 token 失败",
            {"error": error_msg, "consecutive_failures": consecutive},
        )
        return

    try:
        import urllib.request
        msg_url = "https://open.feishu.cn/open-apis/im/v1/messages?receive_id_type=open_id"
        content = f"🦞【紧急】自动回复系统异常\n\n⚠️ 连续失败 {consecutive} 次\n错误:{error_msg}\n时间:{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n请尽快检查系统!"
        data = {
            "receive_id": FEISHU_USER_ID,
            "msg_type": "text",
            "content": json.dumps({"text": content})
        }
        req = urllib.request.Request(
            msg_url,
            data=json.dumps(data).encode(),
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {token}"
            },
            method="POST"
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            result = json.loads(resp.read())
            if result.get("code") == 0:
                log("📨 崩溃报警已发送飞书")
    except Exception as e:
        log(f"发送崩溃报警失败: {e}")
        write_local_warning_alert(
            "crash_alert_failed",
            "飞书崩溃报警发送失败",
            {"error": error_msg, "consecutive_failures": consecutive, "send_error": str(e)},
        )

def update_health(status, error_msg=None):
    """更新健康状态"""
    health = {}
    if os.path.exists(HEALTH_FILE):
        with open(HEALTH_FILE, 'r') as f:
            health = json.load(f)

    prev_consecutive = health.get("consecutive_failures", 0)

    if status == "ok":
        health["consecutive_failures"] = 0
        health["last_ok_time"] = datetime.now().isoformat()
        health["status"] = "running"
        health.pop("last_error", None)
        health.pop("last_error_time", None)
        if os.path.exists(ALERT_FILE):
            os.remove(ALERT_FILE)
    else:
        health["consecutive_failures"] = prev_consecutive + 1
        health["last_error"] = error_msg
        health["last_error_time"] = datetime.now().isoformat()
        health["status"] = "error"

        if health["consecutive_failures"] >= CRON_ALERT_THRESHOLD:
            # 先自检自愈，再决定是否报警
            heal_result = self_heal()
            if heal_result is None:
                # 无法自愈，才发送飞书报警
                alert = {
                    "type": "crash",
                    "time": datetime.now().isoformat(),
                    "error": error_msg,
                    "consecutive_failures": health["consecutive_failures"]
                }
                with open(ALERT_FILE, 'w') as f:
                    json.dump(alert, f, ensure_ascii=False)
                send_crash_alert(error_msg, health["consecutive_failures"])
            else:
                log(f"🔧 自愈生效，错误已修复（{heal_result}），本次不报警")
                # 重置失败计数，避免连续报警
                health["consecutive_failures"] = 0

    with open(HEALTH_FILE, 'w') as f:
        json.dump(health, f, ensure_ascii=False, indent=2)

def main():
    mode = "🔍 灰度观察" if DRY_RUN else "🚀 正式接管"
    log(f"========== 自动回复检查 {mode} ==========")

    try:
        unread = check_unread_count()
        if unread is None:
            log("❌ 无法获取未读数")
            update_health("error", "无法获取未读数")
            return

        log(f"未读数: {unread}")

        # 在确认当前登录态可用后，再做 Cookie 过期提醒判断，避免误报。
        check_cookie_expiry(auth_verified=True)

        if unread <= 0:
            log("✅ 未读数为 0，跳过浏览器处理")
            update_health("ok")
            return

        count = process_conversations_via_browser()
        if count == 0:
            log("✅ 处理完成(无新会话或全被防重跳过)")
            update_health("ok")
        else:
            log(f"📤 已处理 {count} 个会话{' [DRY_RUN 仅记录] ' if DRY_RUN else ''}")
            update_health("ok")

    except Exception as e:
        log(f"❌ 异常: {e}")
        import traceback
        traceback.print_exc()
        update_health("error", str(e))

if __name__ == "__main__":
    main()
