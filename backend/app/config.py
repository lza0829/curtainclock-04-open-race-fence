from __future__ import annotations

import os
from pathlib import Path

PROJECT_NAME = "幕窗钟"
_DEFAULT_DB = Path(__file__).resolve().parents[2] / "data" / "curtainclock.db"
# 夹具与冒烟可用环境变量指到临时库，避免污染本地数据
DB_PATH = Path(os.environ.get("CURTAINCLOCK_DB") or _DEFAULT_DB)
