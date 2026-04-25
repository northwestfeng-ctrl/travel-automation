#!/usr/bin/env python3
"""
Pure reply logic for hotel auto-reply.
"""
from __future__ import annotations

import re


BLOCKED_CORPUS_PATTERNS = [
    "手头有点事",
    "稍微离开一下",
    "回头我会回复您",
    "等等哈",
    "房源笔记",
    "加v",
    "加V",
    "vx",
    "微信",
    "留一下联系方式",
]

BLOCKED_REPLY_PATTERNS = BLOCKED_CORPUS_PATTERNS + [
    "我是王依凡",
    "稍后我来解答",
    "稍后主动联系您",
]

CONTACT_ESCALATION_REPLY = "请留下您的联系方式，管家第一时间致电"
CONTACT_ESCALATION_INTENT_PATTERNS = [
    CONTACT_ESCALATION_REPLY,
    "留下您的联系方式",
    "留一下联系方式",
    "联系方式",
    "手机号",
    "手机号码",
]
ACKNOWLEDGEMENT_PATTERNS = [
    "好的",
    "好",
    "谢谢",
    "多谢",
    "嗯嗯",
    "嗯",
    "收到",
    "行",
    "可以",
    "知道了",
    "了解",
    "明白",
    "好的好的",
]
ACKNOWLEDGEMENT_CONTAINS_PATTERNS = [
    "好的",
    "谢谢",
    "多谢",
    "嗯嗯",
    "收到",
    "知道了",
    "了解",
    "明白",
]


def contains_any(text: str, keywords: list[str]) -> bool:
    return any(keyword in text for keyword in keywords)


def normalize_message(text: str) -> str:
    normalized = (text or "").strip().lower()
    normalized = normalized.replace(" ", "")
    normalized = normalized.replace("？", "?").replace("！", "!")
    return normalized


def is_acknowledgement_message(text: str) -> bool:
    if not text:
        return False
    if any(mark in text for mark in ["?", "？"]):
        return False
    if text in ACKNOWLEDGEMENT_PATTERNS:
        return True
    return len(text) <= 8 and contains_any(text, ACKNOWLEDGEMENT_CONTAINS_PATTERNS)


def is_allowed_corpus_pair(pair: dict) -> bool:
    question = str(pair.get("q", "")).strip()
    answer = str(pair.get("a", "")).strip()
    if not question or not answer:
        return False
    if contains_any(answer.lower(), [pattern.lower() for pattern in BLOCKED_CORPUS_PATTERNS]):
        return False
    if answer in {"你好", "您好", "亲"}:
        return False
    return True


def format_corpus_examples(corpus: list[dict]) -> str:
    if not corpus:
        return "(暂无历史语料)"
    parts = []
    for pair in corpus:
        parts.append(f'客人问: {pair["q"]}\n客服答: {pair["a"]}')
    return "\n---\n".join(parts)


def fallback_unknown_reply() -> str:
    return f"亲,麻烦发下入住日期、人数或具体房型,我帮您确认哦~{CONTACT_ESCALATION_REPLY}"


def escalation_reply(prefix: str) -> str:
    return f"{prefix}{CONTACT_ESCALATION_REPLY}"


def has_contact_escalation_intent(text: str) -> bool:
    if contains_any(text, CONTACT_ESCALATION_INTENT_PATTERNS):
        return True
    return "管家" in text and ("致电" in text or "联系" in text)


def build_service_logic_reply(guest_message: str, facts: dict) -> str:
    msg = normalize_message(guest_message)

    if not msg:
        return fallback_unknown_reply()
    if is_acknowledgement_message(msg):
        return "亲,好的~请问还有什么需要帮忙的吗?"
    if contains_any(msg, ['在吗', '在不', '在嘛', '有人吗', '有人在吗']):
        return "亲,在的~请问想咨询哪天入住或什么房型呢?"
    if contains_any(msg, ['多少钱', '多钱', '价格', '价位', '房费', '一晚多少', '一晚上多少']):
        return "亲,麻烦发下入住日期和人数,我帮您看下价格哦~"
    if contains_any(msg, ['有房吗', '还有房吗', '还能订吗', '可以订吗', '能住吗', '房态', '满房']):
        return "亲,麻烦发下入住日期和人数,我帮您看下房态哦~"
    if (
        re.search(r'\d+大\d+小', msg)
        or re.search(r'\d+人', msg)
        or contains_any(msg, ['几个人', '几人', '住几个人', '可住几人', '几大几小', '大人', '小孩', '儿童'])
    ) and contains_any(msg, ['入住', '能住', '住得下', '适合', '可以住', '可以入住']):
        return "亲,麻烦发下入住日期、人数和具体房型,我帮您确认是否适合入住哦~"
    if contains_any(msg, ['停车', '免费停车']):
        if facts.get("parking_text"):
            return f"亲,您好!我们这边{facts['parking_text']}~请问想预订什么日期入住呢?"
        return escalation_reply("亲,麻烦发下入住日期,我帮您确认停车安排哦~")
    if contains_any(msg, ['地址', '位置', '在哪里', '怎么走', '导航']):
        if facts.get("address"):
            return f"亲,我们这边地址是{facts['address']}~您到了附近也可以再联系我。"
        return escalation_reply("亲,麻烦发下您从哪里过来,我帮您确认路线哦~")
    if contains_any(msg, ['wi-fi', 'wifi', '无线网', '无线', '网怎么样', '有网吗', '网络怎么样', '网速', '有网络吗', '信号', '信号差', '信号不好']):
        if facts.get("wifi_text"):
            return f"亲,房间这边{facts['wifi_text']}哦~请问需要预订吗?"
        return escalation_reply("亲,麻烦发下您看的房型,我帮您确认网络情况哦~")
    if contains_any(msg, ['空调', '冷气', '暖气', '制热', '制冷']):
        if facts.get("aircon_text"):
            return f"亲,房间这边{facts['aircon_text']}的~请问想预订什么日期呢?"
        return escalation_reply("亲,麻烦发下您看的房型,我帮您确认房间配置哦~")
    if '早餐' in msg:
        if facts.get("breakfast_available") is False:
            return "亲,早餐情况以实际预订页展示为准,您发下日期我帮您看哦~"
        if facts.get("breakfast_available"):
            parts = ["亲,我们这边有早餐的"]
            if facts.get("breakfast_hours"):
                parts.append(f"时间是{facts['breakfast_hours']}")
            if facts.get("breakfast_price"):
                parts.append(f"费用是{facts['breakfast_price']}")
            return "，".join(parts) + "~请问需要预订吗?"
        return escalation_reply("亲,麻烦发下您看的房型和日期,我帮您确认早餐情况哦~")
    if contains_any(msg, ['接站', '接机', '接送', '送站', '送机']):
        if facts.get("pickup_text"):
            return f"亲,我们这边{facts['pickup_text']}，需要的话把到达时间发我,我帮您确认下~"
        return escalation_reply("亲,麻烦发下到达时间和人数,我帮您确认接送安排哦~")
    if contains_any(msg, ['提前入住', '提前到', '早到', '能不能提前入住', '可以提前入住']):
        if facts.get("checkin_time"):
            return escalation_reply(f"亲,正常入住时间是{facts['checkin_time']}，提前入住需要看当天房态哦~")
        return escalation_reply("亲,麻烦发下入住日期和房型,我帮您确认能否提前入住哦~")
    if contains_any(msg, ['入住时间', '退房时间', '几点入住', '几点退房', '几点可以入住', '几点退房']):
        if facts.get("checkin_time") or facts.get("checkout_time"):
            parts = []
            if facts.get("checkin_time"):
                parts.append(f"入住时间是{facts['checkin_time']}")
            if facts.get("checkout_time"):
                parts.append(f"退房时间是{facts['checkout_time']}")
            return "亲," + "，".join(parts) + "~请问想预订什么日期呢?"
        return escalation_reply("亲,麻烦发下您看的房型,我帮您确认入住和退房时间哦~")
    if contains_any(msg, ['取消', '退款', '改期']) or ('退' in msg and '退房' not in msg):
        return "亲,麻烦发下订单信息或房型日期,我帮您确认取消改期规则哦~"
    if contains_any(msg, ['宠物', '带狗', '带猫']):
        if facts.get("pets_allowed") is True:
            suffix = facts.get("pets_text") or "允许携带宠物"
            return f"亲,我们这边{suffix}哦~请问想预订什么日期呢?"
        if facts.get("pets_allowed") is False:
            return "亲,您好!我们这边暂时不支持携带宠物哦,辛苦您知悉下~"
        return escalation_reply("亲,麻烦发下您看的房型和日期,我帮您确认宠物政策哦~")
    if contains_any(msg, ['泳池', '游泳']):
        if facts.get("pool_text"):
            return f"亲,我们这边{facts['pool_text']}~请问需要预订吗?"
        return escalation_reply("亲,麻烦发下您看的房型,我帮您确认泳池情况哦~")
    if contains_any(msg, ['餐厅', '吃饭', '用餐', '厨房', '做饭']):
        if facts.get("restaurant_text"):
            return f"亲,我们这边{facts['restaurant_text']}~请问需要预订吗?"
        return escalation_reply("亲,麻烦发下您看的房型,我帮您确认用餐配套哦~")
    if contains_any(msg, ['加床', '加被子', '多一床被子', '多要被子', '加一床被子']):
        return escalation_reply("亲,麻烦发下入住日期、人数和具体房型,我帮您确认加床加被安排哦~")
    if contains_any(msg, ['海景', '看海', '浴缸', '投影', '阳台', '露台', '洗衣机', '冰箱']):
        return escalation_reply("亲,麻烦发下您看的具体房型,我帮您确认房间配置哦~")
    if contains_any(msg, ['订单', '预订', '下单', '付款', '支付']):
        return "亲,麻烦发下入住日期和人数,我帮您确认预订信息哦~"
    return fallback_unknown_reply()


def sanitize_generated_reply(reply: str, base_reply: str) -> str:
    cleaned = (reply or "").strip()
    if not cleaned:
        return base_reply
    if len(cleaned) > 60:
        return base_reply
    if contains_any(cleaned.lower(), [pattern.lower() for pattern in BLOCKED_REPLY_PATTERNS]):
        return base_reply
    if re.search(r'1[3-9]\d{9}', cleaned):
        return base_reply
    if CONTACT_ESCALATION_REPLY not in base_reply and has_contact_escalation_intent(cleaned):
        return base_reply
    if CONTACT_ESCALATION_REPLY in base_reply:
        has_escalation_intent = all(keyword in cleaned for keyword in ["联系", "管家", "致电"])
        if not has_escalation_intent:
            return base_reply
    return cleaned
