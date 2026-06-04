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
TABLE_SEPARATOR_RE = re.compile(r"^:?-{3,}:?$")
TARGET_HEADERS = {"target", "目标"}
DEFINITION_HEADERS = {
    "definition",
    "targetdefinition",
    "目标定义",
    "目标简洁定义",
    "目标简要定义",
    "简洁定义",
}
MISSING_DEFINITIONS = {
    "",
    "-",
    "—",
    "n/a",
    "na",
    "none",
    "tbd",
    "unknown",
    "无",
    "待定",
    "待定义",
    "待补充",
    "待生成",
    "未定义",
}


def parse_target(raw: str) -> str:
    raw = raw.strip()
    if raw.startswith("<") and ">" in raw:
        return raw[1 : raw.index(">")]
    return raw.split(maxsplit=1)[0]


def split_table_row(line: str) -> list[str]:
    stripped = line.strip()
    if "|" not in stripped:
        return []
    if stripped.startswith("|"):
        stripped = stripped[1:]
    if stripped.endswith("|"):
        stripped = stripped[:-1]
    return [cell.replace(r"\|", "|").strip() for cell in re.split(r"(?<!\\)\|", stripped)]


def normalize_header(cell: str) -> str:
    return re.sub(r"[\s`*_]+", "", cell).casefold()


def is_separator_row(cells: list[str]) -> bool:
    return bool(cells) and all(TABLE_SEPARATOR_RE.fullmatch(cell.strip()) for cell in cells)


def definition_is_missing(cell: str) -> bool:
    normalized = re.sub(r"[\s`*_]+", "", cell).casefold().strip(".,:;，。；：")
    return normalized in MISSING_DEFINITIONS


def inspect_target_definitions(scan_text: str) -> tuple[bool, int, list[str]]:
    lines = scan_text.splitlines()
    found = False
    row_count = 0
    missing: list[str] = []

    for index in range(len(lines) - 1):
        headers = split_table_row(lines[index])
        separator = split_table_row(lines[index + 1])
        if not headers or not is_separator_row(separator) or len(headers) != len(separator):
            continue
        normalized = [normalize_header(cell) for cell in headers]
        target_indexes = [i for i, cell in enumerate(normalized) if cell in TARGET_HEADERS]
        definition_indexes = [
            i for i, cell in enumerate(normalized) if cell in DEFINITION_HEADERS
        ]
        if not target_indexes or not definition_indexes:
            continue

        found = True
        target_index = target_indexes[0]
        definition_index = definition_indexes[0]
        for line in lines[index + 2 :]:
            cells = split_table_row(line)
            if not cells:
                break
            if max(target_index, definition_index) >= len(cells):
                break
            target = cells[target_index].strip()
            if not target:
                continue
            row_count += 1
            if definition_is_missing(cells[definition_index]):
                missing.append(target)

    return found, row_count, missing


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("dashboard", type=Path)
    parser.add_argument("--require-section", action="append", default=[])
    parser.add_argument("--require-target-definitions", action="store_true")
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
    definition_table_found, definition_rows, missing_definitions = inspect_target_definitions(
        scan_text
    )

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

    definition_failure = args.require_target_definitions and (
        not definition_table_found or not definition_rows or bool(missing_definitions)
    )
    result = {
        "dashboard": str(dashboard),
        "status": (
            "passed"
            if not missing_sections and not missing_links and not definition_failure
            else "failed"
        ),
        "checked_local_links": len(checked),
        "missing_sections": missing_sections,
        "missing_links": missing_links,
        "target_definition_table_found": definition_table_found,
        "target_definition_rows": definition_rows,
        "missing_target_definitions": missing_definitions,
    }
    if args.as_json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(
            f"{result['status']}: checked {result['checked_local_links']} local links; "
            f"missing sections={len(missing_sections)}; missing links={len(missing_links)}; "
            f"target definition table={'yes' if definition_table_found else 'no'}; "
            f"definition rows={definition_rows}; "
            f"missing definitions={len(missing_definitions)}"
        )
        for section in missing_sections:
            print(f"missing section: {section}")
        for link in missing_links:
            print(f"missing link: {link}")
        if args.require_target_definitions and not definition_table_found:
            print("missing target definition table")
        elif args.require_target_definitions and not definition_rows:
            print("target definition table has no target rows")
        for target in missing_definitions:
            print(f"missing target definition: {target}")
    return 0 if result["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
