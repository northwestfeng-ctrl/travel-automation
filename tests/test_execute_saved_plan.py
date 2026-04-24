#!/usr/bin/env python3
import sys
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
PRICING_DIR = PROJECT_ROOT / "pricing"
if str(PRICING_DIR) not in sys.path:
    sys.path.insert(0, str(PRICING_DIR))

from execute_saved_plan import validate_plan

if str(PRICING_DIR) in sys.path:
    sys.path.remove(str(PRICING_DIR))
for module_name in (
    "ebooking_batch_price_api",
    "execute_saved_plan",
    "path_config",
    "recommendation_to_execution_plan",
):
    sys.modules.pop(module_name, None)


class ExecuteSavedPlanGuardrailTests(unittest.TestCase):
    def test_validate_plan_prevents_negative_margin(self):
        plan = {
            "planGroups": [
                {
                    "sourceRoomName": "测试大床房",
                    "operations": [
                        {
                            "roomProductId": "rp_001",
                            "currentEbookingSalePrice": 500,
                            "targetSalePrice": 500,
                            "targetCostPrice": 550,
                        }
                    ],
                }
            ]
        }

        errors = validate_plan(plan, max_ops=20)

        self.assertTrue(
            any("target cost price exceeds target sale price" in error for error in errors),
            errors,
        )


if __name__ == "__main__":
    unittest.main()
