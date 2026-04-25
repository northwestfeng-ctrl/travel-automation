#!/usr/bin/env python3
import importlib.util
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


PROJECT_ROOT = Path(__file__).resolve().parents[1]
COMPETITOR_DIR = PROJECT_ROOT / "competitor-analysis"


def load_competitor_module(module_name: str, filename: str):
    spec = importlib.util.spec_from_file_location(module_name, COMPETITOR_DIR / filename)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


class CompetitorPathConfigTests(unittest.TestCase):
    def test_results_dir_defaults_to_project_relative_path(self):
        module = load_competitor_module("competitor_path_config", "path_config.py")
        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual(module.competitor_results_dir(), COMPETITOR_DIR / "results")

    def test_results_dir_prefers_env(self):
        module = load_competitor_module("competitor_path_config_env", "path_config.py")
        with patch.dict(os.environ, {"COMPETITOR_RESULTS_DIR": "~/ctrip-results"}):
            self.assertEqual(module.competitor_results_dir(), Path("~/ctrip-results").expanduser())

    def test_scraper_compiles_and_imports_without_absolute_project_path(self):
        load_competitor_module("scrape_ctrip_v2_for_test", "scrape_ctrip_v2.py")

    def test_scraper_loads_cookies_from_config_file(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            config_file = Path(tmpdir) / "config.json"
            config_file.write_text(
                json.dumps(
                    {
                        "cookies": [
                            {
                                "domain": ".ctrip.com",
                                "name": "cticket",
                                "path": "/",
                                "value": "test_ticket",
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )
            with patch.dict(os.environ, {"COMPETITOR_CONFIG_FILE": str(config_file)}):
                module = load_competitor_module("scrape_ctrip_v2_cookie_test", "scrape_ctrip_v2.py")

        self.assertEqual(module.COOKIES[0]["name"], "cticket")
        self.assertEqual(module.COOKIES[0]["value"], "test_ticket")

    def test_scraper_parses_cookie_header_from_config_file(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            config_file = Path(tmpdir) / "config.json"
            config_file.write_text(
                json.dumps({"cookie": "cticket=test_ticket; usertoken=test_token"}),
                encoding="utf-8",
            )
            with patch.dict(os.environ, {"COMPETITOR_CONFIG_FILE": str(config_file)}):
                module = load_competitor_module("scrape_ctrip_v2_cookie_header_test", "scrape_ctrip_v2.py")

        self.assertEqual([cookie["name"] for cookie in module.COOKIES], ["cticket", "usertoken"])


if __name__ == "__main__":
    unittest.main()
