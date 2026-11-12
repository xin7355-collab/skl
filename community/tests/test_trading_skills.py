# skl 自寫：跑 tradermonty/claude-trading-skills 5 個技能的上游測試
# （上游測試 MIT，Copyright (c) 2026 TraderMonty，原樣放在 trading/<技能>/，日後同步上游直接整個覆蓋）。
#
# 為什麼不直接 pytest trading/：上游測試假設自己在 skills/<技能>/scripts/tests/，用 parents[1] 找腳本、
# position-sizer 有一項用「repo 根目錄 + 相對路徑」呼叫腳本並預期失敗——路徑不對時 python 本來就會失敗，
# 那一項就變成假綠燈。所以每次在暫存資料夾照上游的樣子重組（skl 這份技能 + 上游測試），
# 每個技能各開一個 pytest 子行程：互不污染 sys.path，也不會在技能資料夾留下 __pycache__。
"""Offline tests for the 5 tradermonty trading skills (upstream tests, rebuilt into the upstream layout)."""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from _skill_helpers import need, skill_root

TIMEOUT = 300
TESTS = Path(__file__).resolve().parent / "trading"
SKILLS = ("backtest-expert", "residual-edge-analyzer", "edge-strategy-reviewer", "trade-performance-coach", "position-sizer")
# 不跑的上游測試要寫理由；也要真的被排除到（下面會核對 deselected 數量），上游改名就會紅燈提醒。
DESELECT = {
    # 比對上游 repo 的 docs/dev/strategy-research-bias-checklist.md 跟技能附的清單一致；
    # 那份文件在上游 repo 根目錄、不屬於技能本體，skl 沒收。
    "edge-strategy-reviewer": ["test_review_strategy_drafts.py::test_bias_checklist_doc_matches_asset"],
}
# 每個技能至少要跑到這麼多項（目前上游各有 30～90 項）；收集不到測試、或被大量排除時不能算綠燈。
MIN_PASSED = 20


def _summary(text: str) -> dict:
    """pytest -q 最後一行，例如 '57 passed, 1 deselected in 0.42s' → {'passed': 57, 'deselected': 1}。"""
    last = [ln for ln in text.strip().splitlines() if re.search(r"\d+ (passed|failed|error)", ln)]
    if not last:
        return {}
    return {k: int(n) for n, k in re.findall(r"(\d+) (passed|failed|errors?|skipped|deselected|xfailed|xpassed)", last[-1])}


class TradingSkillUpstreamTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        # 缺套件就失敗、不 skip：上游測試自己遇到缺 jsonschema 會 skip，那一項就什麼都沒驗到。
        need("yaml", "edge-strategy-reviewer 的腳本與測試")
        need("jsonschema", "trade-performance-coach 用 schema 驗輸出")

    def test_upstream_suites(self) -> None:
        for name in SKILLS:
            with self.subTest(skill=name):
                with tempfile.TemporaryDirectory() as tmp:
                    root = Path(tmp)
                    skill = root / "skills" / name
                    shutil.copytree(skill_root(name), skill, ignore=shutil.ignore_patterns("__pycache__"))
                    shutil.copytree(TESTS / name, skill / "scripts" / "tests")
                    # --deselect 比對的是「相對 rootdir 的 node id」：固定 rootdir，不靠 pytest 自己猜。
                    args = [sys.executable, "-m", "pytest", "-q", "-rs", "-p", "no:cacheprovider",
                            "--import-mode=importlib", "--rootdir", str(root), str(skill / "scripts" / "tests")]
                    for node in DESELECT.get(name, []):
                        args += ["--deselect", f"skills/{name}/scripts/tests/{node}"]
                    r = subprocess.run(args, capture_output=True, text=True, timeout=TIMEOUT, cwd=root,
                                       env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"})
                    out = r.stdout + r.stderr
                    got = _summary(r.stdout)
                    self.assertEqual(r.returncode, 0, f"{name} 上游測試失敗：\n{out[-3000:]}")
                    self.assertGreaterEqual(got.get("passed", 0), MIN_PASSED, f"{name} 跑到的測試太少：{got}\n{out[-1500:]}")
                    self.assertEqual(got.get("skipped", 0), 0, f"{name} 有測試被 skip（多半是缺套件）：\n{out[-1500:]}")
                    self.assertEqual(got.get("deselected", 0), len(DESELECT.get(name, [])),
                                     f"{name} 排除清單沒對上（上游改名或刪掉了？）：{got}")


if __name__ == "__main__":
    unittest.main()
