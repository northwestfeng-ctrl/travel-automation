#!/usr/bin/env python3
import importlib
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import pricing.path_config as path_config


class PricingPathConfigTests(unittest.TestCase):
    def tearDown(self):
        importlib.reload(path_config)

    def test_default_storage_state_prefers_env(self):
        with patch.dict(os.environ, {"CTRIP_STORAGE_STATE": "~/custom-state.json"}):
            importlib.reload(path_config)
            self.assertEqual(
                path_config.default_storage_state_path(),
                Path("~/custom-state.json").expanduser(),
            )

    def test_captured_requests_path_prefers_env(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            configured = Path(tmpdir) / "capture.json"
            with patch.dict(os.environ, {"CTRIP_CAPTURED_REQUESTS_FILE": str(configured)}):
                self.assertEqual(path_config.captured_requests_path("capture_api"), configured)

    def test_captured_requests_path_uses_artifacts_timestamp(self):
        with patch.dict(os.environ, {}, clear=True):
            path = path_config.captured_requests_path("capture api")
        self.assertEqual(path.parent, path_config.ARTIFACTS_DIR)
        self.assertIn("capture_api_captured_requests_", path.name)
        self.assertEqual(path.suffix, ".json")

    def test_pricing_modules_import_as_package(self):
        modules = [
            "pricing.ebooking_batch_price_api",
            "pricing.recommendation_to_execution_plan",
            "pricing.capture_api",
            "pricing.capture_api_v2",
            "pricing.capture_direct",
            "pricing.capture_proxy",
            "pricing.capture_batch_price_flow",
            "pricing.analyze_rateplan_bundle",
        ]
        for module_name in modules:
            with self.subTest(module_name=module_name):
                importlib.import_module(module_name)

    def test_capture_batch_resolves_env_storage_state(self):
        from pricing.capture_batch_price_flow import resolve_storage_state

        with patch.dict(os.environ, {"CTRIP_STORAGE_STATE": "~/capture-state.json"}):
            self.assertEqual(
                resolve_storage_state(None),
                Path("~/capture-state.json").expanduser(),
            )


if __name__ == "__main__":
    unittest.main()
