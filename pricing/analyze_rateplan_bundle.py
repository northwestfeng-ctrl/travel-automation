#!/usr/bin/env python3
"""
Scan the public ht-rateplan frontend bundles and extract route / endpoint strings.

This helps narrow reverse-engineering work before doing a live capture.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import urllib.request
from collections import defaultdict
from datetime import datetime
from typing import Any

try:
    from path_config import ARTIFACTS_DIR, PROJECT_ROOT
except ModuleNotFoundError:
    from pricing.path_config import ARTIFACTS_DIR, PROJECT_ROOT

DEFAULT_MANIFEST_URL = (
    "https://bd-s.tripcdn.cn/modules/EBooking/100036498-ht-rateplan/"
    "asset-manifest.b1719734fd313c79a6f95a0b07a2f954.json"
)
PATTERNS = [
    re.compile(r"/restapi/soa2/\d+[^\s\"'<>]*"),
    re.compile(r"/ebkovsroom/api[^\s\"'<>]*"),
    re.compile(r"/[A-Za-z0-9?=&_./-]*(?:rateplan|price|inventory|room|policy|task)[A-Za-z0-9?=&_./-]*"),
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Extract rateplan-related strings from the public bundles.")
    parser.add_argument(
        "--manifest-url",
        default=os.environ.get("CTRIP_RATEPLAN_MANIFEST_URL", DEFAULT_MANIFEST_URL),
        help="Full asset-manifest URL for the ht-rateplan micro-frontend.",
    )
    return parser.parse_args()


def fetch_text(url: str) -> str:
    with urllib.request.urlopen(url) as response:
        return response.read().decode("utf-8", errors="replace")


def build_asset_url(manifest_url: str, asset_path: str) -> str:
    prefix = manifest_url.rsplit("/", 1)[0]
    return f"{prefix}{asset_path}"


def main() -> int:
    args = parse_args()
    ARTIFACTS_DIR.mkdir(parents=True, exist_ok=True)

    manifest = json.loads(fetch_text(args.manifest_url))
    files = manifest.get("files", {})
    js_assets = sorted(path for path in files.values() if path.endswith(".js"))

    by_asset: dict[str, list[str]] = {}
    aggregated: dict[str, set[str]] = defaultdict(set)

    for asset_path in js_assets:
        asset_url = build_asset_url(args.manifest_url, asset_path)
        text = fetch_text(asset_url)

        matches: set[str] = set()
        for pattern in PATTERNS:
            matches.update(pattern.findall(text))

        cleaned = sorted(
            match
            for match in matches
            if len(match) > 3 and "sourceMappingURL" not in match
        )
        if not cleaned:
            continue

        by_asset[asset_path] = cleaned
        for match in cleaned:
            aggregated[match].add(asset_path)

    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    json_path = ARTIFACTS_DIR / f"rateplan_bundle_report_{stamp}.json"
    md_path = ARTIFACTS_DIR / f"rateplan_bundle_report_{stamp}.md"

    report: dict[str, Any] = {
        "created_at": datetime.now().isoformat(),
        "manifest_url": args.manifest_url,
        "asset_count": len(js_assets),
        "matched_asset_count": len(by_asset),
        "matches_by_asset": by_asset,
        "assets_by_match": {match: sorted(paths) for match, paths in sorted(aggregated.items())},
    }
    json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    interesting = [
        match
        for match in sorted(aggregated)
        if any(token in match.lower() for token in ("batch", "save", "task", "inventory", "price"))
    ]
    markdown_lines = [
        "# ht-rateplan Bundle Scan",
        "",
        f"- 生成时间: {datetime.now().isoformat()}",
        f"- Manifest: {args.manifest_url}",
        f"- 扫描 JS 资源数: {len(js_assets)}",
        f"- 命中资源数: {len(by_asset)}",
        "",
        "## 重点字符串",
        "",
    ]
    for match in interesting[:50]:
        markdown_lines.append(f"- `{match}`")
        markdown_lines.append(f"  来源: {', '.join(sorted(aggregated[match]))}")

    md_path.write_text("\n".join(markdown_lines) + "\n", encoding="utf-8")

    print(f"Saved JSON report: {json_path}")
    print(f"Saved Markdown report: {md_path}")
    print("Top findings:")
    for match in interesting[:20]:
        print(f"  - {match}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
