#!/usr/bin/env python3
"""Validate local links and required sections in a Markdown results dashboard."""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from urllib.parse import unquote, urlsplit


LINK_RE = re.compile(r"!?\[[^\]]*\]\(([^)]+)\)")
FENCE_RE = re.compile(r"```.*?```", re.DOTALL)


def parse_target(raw: str) -> str:
    raw = raw.strip()
    if raw.startswith("<") and ">" in raw:
        return raw[1 : raw.index(">")]
    return raw.split(maxsplit=1)[0]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("dashboard", type=Path)
    parser.add_argument("--require-section", action="append", default=[])
    parser.add_argument("--json", action="store_true", dest="as_json")
    args = parser.parse_args()

    dashboard = args.dashboard.resolve()
    text = dashboard.read_text(encoding="utf-8")
    scan_text = FENCE_RE.sub("", text)

    missing_sections = [
        section for section in args.require_section if section not in text
    ]
    checked: list[str] = []
    missing_links: list[str] = []

    for match in LINK_RE.finditer(scan_text):
        target = parse_target(match.group(1))
        split = urlsplit(target)
        if split.scheme or target.startswith(("#", "mailto:")):
            continue
        local = unquote(split.path)
        if not local:
            continue
        path = Path(local)
        if not path.is_absolute():
            path = dashboard.parent / path
        checked.append(target)
        if not path.exists():
            missing_links.append(target)

    result = {
        "dashboard": str(dashboard),
        "status": "passed" if not missing_sections and not missing_links else "failed",
        "checked_local_links": len(checked),
        "missing_sections": missing_sections,
        "missing_links": missing_links,
    }
    if args.as_json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(
            f"{result['status']}: checked {result['checked_local_links']} local links; "
            f"missing sections={len(missing_sections)}; missing links={len(missing_links)}"
        )
        for section in missing_sections:
            print(f"missing section: {section}")
        for link in missing_links:
            print(f"missing link: {link}")
    return 0 if result["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
