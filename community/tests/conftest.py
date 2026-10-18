"""社群技能測試的共用設定。

為什麼測試放在 community/tests/ 而不是技能資料夾裡：app-bootstrap 會把整個技能資料夾複製到
別的 App 的 repo，測試放進去就會跟著被複製，還會把 pytest 等開發套件變成那些 App 的負擔。

改寫自 K-Dense scientific-agent-skills 的 tests/conftest.py（MIT，Copyright (c) 2025 K-Dense Inc.，
見同資料夾 LICENSE.md）：只留「不寫 .pyc」與「畫圖不開視窗」兩件事。上游那套 skill_contract
（tests/_contract）沒有搬過來，需要的兩個檢查改寫在 _skill_helpers.py。
"""

import os
import sys

# 測試會 import 技能的 scripts/，Python 預設會在技能資料夾裡留下 __pycache__，
# 下次 bootstrap 複製技能時就會把它一起帶走；子行程（--help、demo）也一樣關掉。
sys.dont_write_bytecode = True
os.environ["PYTHONDONTWRITEBYTECODE"] = "1"
# CI 沒有螢幕：matplotlib 一律用不開視窗的 Agg。
os.environ.setdefault("MPLBACKEND", "Agg")
