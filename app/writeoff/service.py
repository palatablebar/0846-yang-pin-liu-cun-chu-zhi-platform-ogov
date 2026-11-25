"""场景样例加载与无状态核对服务。"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from app.config import SAMPLES
from app.writeoff.engine import reconcile

WRITEOFF_SAMPLES = SAMPLES / "writeoff"


def scenario_path(name: str) -> Path:
    # 只允许简单文件名，杜绝路径穿越
    safe = Path(name).name
    return WRITEOFF_SAMPLES / f"{safe}.json"


def list_scenarios() -> list[dict]:
    items = []
    if not WRITEOFF_SAMPLES.exists():
        return items
    for path in sorted(WRITEOFF_SAMPLES.glob("*.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        items.append({
            "name": path.stem,
            "title": data.get("title", path.stem),
            "description": data.get("description", ""),
        })
    return items


def load_scenario(name: str):
    path = scenario_path(name)
    if not path.exists():
        return None, None
    raw = path.read_bytes()
    data = json.loads(raw.decode("utf-8-sig"))
    digest = hashlib.sha256(raw).hexdigest()
    return data, digest


def scenario_board(name: str):
    data, digest = load_scenario(name)
    if data is None:
        return None
    board = reconcile({"orders": data.get("orders"), "receipts": data.get("arrivals")
                       or data.get("receipts")})
    return {"scenario": name, "title": data.get("title", name),
            "description": data.get("description", ""), "as_of": data.get("as_of"),
            "sha256": digest, "board": board}
