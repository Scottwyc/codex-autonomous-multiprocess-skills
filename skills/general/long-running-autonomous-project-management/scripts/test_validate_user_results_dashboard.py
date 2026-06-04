#!/usr/bin/env python3
"""Focused tests for the user-facing key-results dashboard validator."""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


SCRIPT = Path(__file__).with_name("validate_user_results_dashboard.py")


class ValidateUserResultsDashboardTest(unittest.TestCase):
    def run_validator(self, text: str) -> tuple[subprocess.CompletedProcess[str], dict]:
        with tempfile.TemporaryDirectory() as tmp:
            dashboard = Path(tmp) / "dashboard.md"
            dashboard.write_text(text, encoding="utf-8")
            result = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPT),
                    str(dashboard),
                    "--require-target-definitions",
                    "--json",
                ],
                check=False,
                capture_output=True,
                text=True,
            )
        return result, json.loads(result.stdout)

    def test_accepts_nonempty_target_definitions(self) -> None:
        result, payload = self.run_validator(
            """# Dashboard

| Target | 目标简洁定义 | 状态 |
|---|---|---|
| T1 | 建立严格协议下的跨域矩阵。 | 进行中 |
| T2 | 验证 hard scene 的性能边界。 | 边界完成 |
"""
        )
        self.assertEqual(result.returncode, 0)
        self.assertEqual(payload["status"], "passed")
        self.assertEqual(payload["target_definition_rows"], 2)
        self.assertEqual(payload["missing_target_definitions"], [])

    def test_rejects_missing_definition_column(self) -> None:
        result, payload = self.run_validator(
            """# Dashboard

| Target | 状态 |
|---|---|
| T1 | 进行中 |
"""
        )
        self.assertEqual(result.returncode, 1)
        self.assertFalse(payload["target_definition_table_found"])

    def test_rejects_placeholder_definition(self) -> None:
        result, payload = self.run_validator(
            """# Dashboard

| Target | Definition | Status |
|---|---|---|
| T1 | TBD | active |
"""
        )
        self.assertEqual(result.returncode, 1)
        self.assertEqual(payload["missing_target_definitions"], ["T1"])


if __name__ == "__main__":
    unittest.main()
