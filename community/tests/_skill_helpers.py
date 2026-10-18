"""兩個共用檢查：技能附的指令列工具 --help 能跑、函式庫腳本的示範區塊能跑完。

取代上游 K-Dense tests/_contract（skill_contract）的 cli.help_test_case 與 cli.demo_test_case
（MIT，Copyright (c) 2025 K-Dense Inc.）。上游會在缺套件時 skip；這裡改成直接失敗：
工作流已經照 requirements.txt 裝好固定版本，缺套件代表 requirements.txt 漏寫，
悄悄 skip 只會讓測試看起來綠燈、其實什麼都沒驗到。
"""

from __future__ import annotations

import ast
import importlib
import os
import subprocess
import sys
import unittest
from pathlib import Path

TIMEOUT = 120
SKILLS = Path(__file__).resolve().parents[1] / "skills"
NOT_CLI = {"__init__.py", "__main__.py", "_common.py", "conftest.py"}


def skill_root(name: str) -> Path:
    return SKILLS / name


def need(module: str, reason: str | None = None):
    """取代 pytest.importorskip：缺套件就失敗並說要補哪裡，不默默 skip。"""
    try:
        return importlib.import_module(module)
    except ImportError as e:
        raise AssertionError(
            f"缺少 {module}（{reason or '測試需要'}）：請把它的固定版本加進 community/tests/requirements.txt"
        ) from e


def _env() -> dict:
    return {**os.environ, "PYTHONDONTWRITEBYTECODE": "1", "MPLBACKEND": "Agg"}


def cli_scripts(root: Path) -> list[Path]:
    """scripts/ 第一層有 import argparse 的檔案；只用 ast 讀，不執行。"""
    found = []
    for path in sorted((root / "scripts").glob("*.py")):
        if path.name in NOT_CLI:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if (isinstance(node, ast.Import) and any(a.name == "argparse" for a in node.names)) or (
                isinstance(node, ast.ImportFrom) and node.module == "argparse"
            ):
                found.append(path)
                break
    return found


def help_test_case(root: Path) -> type[unittest.TestCase]:
    class CliHelpTests(unittest.TestCase):
        def test_every_cli_answers_help(self) -> None:
            scripts = cli_scripts(root)
            self.assertTrue(scripts, f"{root.name} 的 scripts/ 找不到指令列工具")
            for script in scripts:
                with self.subTest(script=script.name):
                    # 各自開子行程、在腳本自己的資料夾執行：--help 不該依賴測試行程的 sys.path，
                    # 卡住的腳本也只會讓這一項逾時，不會拖住整個測試。
                    r = subprocess.run([sys.executable, str(script), "--help"], capture_output=True,
                                       text=True, timeout=TIMEOUT, env=_env(), cwd=script.parent)
                    self.assertEqual(r.returncode, 0, f"{script.name} --help 失敗：\n{r.stderr.strip()}")
                    self.assertIn("usage:", r.stdout.lower())
                    self.assertNotIn("Traceback", r.stderr)

    CliHelpTests.__qualname__ = f"CliHelpTests[{root.name}]"
    return CliHelpTests


def demo_test_case(root: Path, scripts: tuple[str, ...]) -> type[unittest.TestCase]:
    """跑 `if __name__ == "__main__":` 的示範：那是寫給人看的用法，套件改版時最先壞。"""

    class DemoBlockTests(unittest.TestCase):
        def test_every_demo_block_runs_clean(self) -> None:
            for name in scripts:
                script = root / "scripts" / name
                with self.subTest(script=name):
                    self.assertTrue(script.is_file(), f"{name} 不在技能裡")
                    r = subprocess.run([sys.executable, str(script)], capture_output=True, text=True,
                                       timeout=TIMEOUT, env=_env(), cwd=script.parent)
                    self.assertEqual(r.returncode, 0, f"{name} 結束碼 {r.returncode}：\n{r.stderr.strip()}")
                    self.assertTrue(r.stdout.strip(), f"{name} 什麼都沒印")

    DemoBlockTests.__qualname__ = f"DemoBlockTests[{root.name}]"
    return DemoBlockTests
