#!/usr/bin/env python3
import unittest

from reply_logic import (
    build_service_logic_reply,
    fallback_unknown_reply,
    format_corpus_examples,
    is_allowed_corpus_pair,
    sanitize_generated_reply,
)


class ReplyLogicTests(unittest.TestCase):
    def setUp(self):
        self.facts = {
            "parking_text": "私人停车场（免费）",
            "wifi_text": "高速Wi-Fi",
            "breakfast_available": True,
            "breakfast_hours": "08:00-10:00",
            "breakfast_price": "¥20/份",
            "checkin_time": "14:00 后",
            "checkout_time": "12:00 前",
            "pets_allowed": True,
            "pets_text": "允许携带宠物（需提前联系酒店）免费",
            "address": "福建平潭县岚城乡上楼村上楼377号华夏庄园D区6号楼",
            "restaurant_text": "公共厨房（免费）",
        }

    def test_wifi_common_phrase(self):
        reply = build_service_logic_reply("你们那里网络怎么样", self.facts)
        self.assertIn("高速Wi-Fi", reply)

    def test_occupancy_question(self):
        reply = build_service_logic_reply("我们2大1小，能入住吗？", self.facts)
        self.assertIn("是否适合入住", reply)

    def test_price_question(self):
        reply = build_service_logic_reply("多少钱一晚", self.facts)
        self.assertIn("入住日期和人数", reply)

    def test_breakfast_question(self):
        reply = build_service_logic_reply("有早餐吗", self.facts)
        self.assertIn("08:00-10:00", reply)
        self.assertIn("¥20/份", reply)

    def test_filtered_corpus_pair(self):
        self.assertFalse(is_allowed_corpus_pair({"q": "那在哪看房源笔记？", "a": "手头有点事儿，我稍微离开一下。回头我会回复您。"}))
        self.assertTrue(is_allowed_corpus_pair({"q": "可以停车吗", "a": "方便停车哦。是几号来呢？"}))

    def test_sanitize_generated_reply_blocks_bad_phrases(self):
        base = fallback_unknown_reply()
        bad = "手头有点事儿，我稍微离开一下。回头我会回复您。"
        self.assertEqual(sanitize_generated_reply(bad, base), base)

    def test_unknown_reply_requests_contact(self):
        reply = build_service_logic_reply("你们有投影吗", {})
        self.assertIn("请留下您的联系方式，管家第一时间致电", reply)

    def test_acknowledgement_does_not_request_contact(self):
        for message in ["好的", "谢谢", "嗯嗯", "收到", "行", "好的谢谢"]:
            reply = build_service_logic_reply(message, {})
            self.assertNotIn("请留下您的联系方式，管家第一时间致电", reply)
            self.assertIn("还有什么需要帮忙", reply)

    def test_sanitize_preserves_contact_escalation(self):
        base = build_service_logic_reply("你们有投影吗", {})
        candidate = "亲,麻烦发下您看的具体房型,我帮您确认房间配置哦~"
        self.assertEqual(sanitize_generated_reply(candidate, base), base)

    def test_extra_amenity_phrases(self):
        signal_reply = build_service_logic_reply("你们那边信号好吗", self.facts)
        self.assertIn("高速Wi-Fi", signal_reply)
        bed_reply = build_service_logic_reply("能加床吗", {})
        self.assertIn("加床加被", bed_reply)
        early_reply = build_service_logic_reply("能不能提前入住", self.facts)
        self.assertIn("提前入住", early_reply)

    def test_format_corpus_examples(self):
        text = format_corpus_examples([{"q": "可以停车吗", "a": "方便停车哦。是几号来呢？"}])
        self.assertIn("客人问", text)
        self.assertIn("客服答", text)


if __name__ == "__main__":
    unittest.main()
