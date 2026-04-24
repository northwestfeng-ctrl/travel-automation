#!/usr/bin/env python3
import os
import tempfile
import unittest

from session_extract import EXTRACT_SESSIONS_JS_CONTENT, write_extract_sessions_file


class SessionExtractTests(unittest.TestCase):
    def test_canonical_script_contains_all_reply_patterns(self):
        self.assertIn("亲，有什么问题可以一并留言", EXTRACT_SESSIONS_JS_CONTENT)
        self.assertIn("稍后主动联系您", EXTRACT_SESSIONS_JS_CONTENT)
        self.assertIn("我这边先帮您记录", EXTRACT_SESSIONS_JS_CONTENT)

    def test_write_extract_sessions_file(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = os.path.join(tmpdir, "extract_sessions.js")
            write_extract_sessions_file(path)
            with open(path, "r", encoding="utf-8") as f:
                content = f.read()
            self.assertEqual(content, EXTRACT_SESSIONS_JS_CONTENT)


if __name__ == "__main__":
    unittest.main()
