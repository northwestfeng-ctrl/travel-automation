#!/usr/bin/env python3
import json
import tempfile
import unittest
from pathlib import Path

from pricing.engine import calc_adjustment, load_latest_data, match_rooms


class PricingEngineTests(unittest.TestCase):
    def test_load_latest_data_uses_configurable_results_dir(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            results_dir = Path(tmpdir)
            older = {"competitors": [{"name": "old"}]}
            newer = {"competitors": [{"name": "new"}]}
            (results_dir / "20260422_180000_full.json").write_text(
                json.dumps(older), encoding="utf-8"
            )
            (results_dir / "20260423_180000_full.json").write_text(
                json.dumps(newer), encoding="utf-8"
            )

            data, source_file = load_latest_data(results_dir)

        self.assertEqual(source_file, "20260423_180000_full.json")
        self.assertEqual(data, newer)

    def test_load_latest_data_reports_missing_dir(self):
        with self.assertRaisesRegex(FileNotFoundError, "COMPETITOR_RESULTS_DIR"):
            load_latest_data("/tmp/not-a-real-travel-results-dir")

    def test_zero_price_does_not_crash_matching_or_adjustment(self):
        matches = match_rooms(
            [{"name": "竞品大床房", "price": 0}],
            [{"name": "自家大床房", "price": 0}],
        )
        self.assertEqual(len(matches), 1)

        action, suggested, reason, change = calc_adjustment(0, 0, 100, 100, "")
        self.assertEqual(action, "维持")
        self.assertEqual(suggested, 0)
        self.assertIn("跳过自动建议", reason)
        self.assertEqual(change, "0%")


if __name__ == "__main__":
    unittest.main()
