#!/usr/bin/env python3
import json
import os
import sys
import unittest
from pathlib import Path

os.environ.setdefault("FEISHU_APP_ID", "test_app_id")
os.environ.setdefault("FEISHU_APP_SECRET", "test_app_secret")
os.environ.setdefault("FEISHU_USER_OPEN_ID", "ou_authorized")

from feishu_approval import detect_decision, matches_decision_keyword

PRICING_DIR = Path(__file__).resolve().parents[1] / "pricing"
if str(PRICING_DIR) in sys.path:
    sys.path.remove(str(PRICING_DIR))
for module_name in (
    "ebooking_batch_price_api",
    "execute_saved_plan",
    "path_config",
    "recommendation_to_execution_plan",
):
    sys.modules.pop(module_name, None)


def make_text_message(message_id: str, text: str, open_id: str, create_time: int = 2000):
    return {
        "message_id": message_id,
        "create_time": str(create_time),
        "chat_id": "oc_test",
        "sender": {
            "sender_type": "user",
            "sender_id": {"open_id": open_id},
        },
        "body": {"content": json.dumps({"text": text}, ensure_ascii=False)},
    }


class FeishuApprovalGuardrailTests(unittest.TestCase):
    def test_detect_decision_rejects_invalid_user(self):
        dispatch = {
            "approvalKeywords": ["确认改价"],
            "rejectKeywords": ["取消改价"],
            "user_open_id": "ou_authorized",
            "feishuMessage": {
                "message_id": "om_request",
                "create_time": "1000",
            },
        }
        messages = [
            make_text_message(
                message_id="om_intruder",
                text="确认改价",
                open_id="ou_intruder",
            )
        ]

        self.assertIsNone(detect_decision(dispatch, messages))

    def test_matches_decision_keyword_accepts_safe_short_wrappers(self):
        keywords = ["确认改价", "批准改价"]

        self.assertTrue(matches_decision_keyword("好，确认改价", keywords))
        self.assertTrue(matches_decision_keyword("[引用] 批准改价", keywords))
        self.assertTrue(matches_decision_keyword("确认改价吧", keywords))

    def test_matches_decision_keyword_rejects_ambiguous_long_text(self):
        keywords = ["确认改价"]

        self.assertFalse(matches_decision_keyword("我们确认改价吗？", keywords))
        self.assertFalse(matches_decision_keyword("不要确认改价", keywords))


if __name__ == "__main__":
    unittest.main()
