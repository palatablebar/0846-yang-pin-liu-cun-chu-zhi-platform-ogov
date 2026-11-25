"""交接单核销工作台的 SQLite 存储。

回执以 ``(board_id, receipt_id)`` 为主键，从存储层保证同张回执重试
不会重复登记；核对结果始终由交接单与按序重放的回执重建。
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from app.writeoff.engine import WriteOffLedger


class WriteOffStore:
    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.init()

    def connect(self):
        connection = sqlite3.connect(self.path, timeout=8)
        connection.row_factory = sqlite3.Row
        return connection

    def init(self):
        with self.connect() as db:
            db.executescript(
                """
                CREATE TABLE IF NOT EXISTS wo_boards(
                  board_id TEXT PRIMARY KEY,
                  title TEXT NOT NULL DEFAULT '',
                  orders_json TEXT NOT NULL,
                  created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS wo_receipts(
                  board_id TEXT NOT NULL,
                  receipt_id TEXT NOT NULL,
                  seq INTEGER NOT NULL,
                  payload TEXT NOT NULL,
                  created_at TEXT NOT NULL,
                  PRIMARY KEY(board_id, receipt_id)
                );
                """
            )

    # ---------- boards ----------

    def create_board(self, board_id: str, orders, title: str = "") -> dict:
        now = datetime.now(timezone.utc).isoformat()
        with self.connect() as db:
            db.execute(
                "INSERT OR IGNORE INTO wo_boards(board_id,title,orders_json,created_at) "
                "VALUES(?,?,?,?)",
                (board_id, title, json.dumps(orders, ensure_ascii=False), now),
            )
        return self.get_board(board_id)

    def get_board(self, board_id: str):
        with self.connect() as db:
            row = db.execute("SELECT * FROM wo_boards WHERE board_id=?", (board_id,)).fetchone()
        return dict(row) if row else None

    def list_boards(self):
        with self.connect() as db:
            rows = db.execute("SELECT board_id,title,created_at FROM wo_boards ORDER BY created_at").fetchall()
        return [dict(row) for row in rows]

    def delete_board(self, board_id: str) -> None:
        with self.connect() as db:
            db.execute("DELETE FROM wo_receipts WHERE board_id=?", (board_id,))
            db.execute("DELETE FROM wo_boards WHERE board_id=?", (board_id,))

    # ---------- receipts ----------

    def add_receipt(self, board_id: str, payload: dict):
        """幂等插入。返回 (row, created: bool)。

        以 ``(board_id, receipt_id)`` 主键与 ``INSERT ... ON CONFLICT DO NOTHING``
        保证并发重试下同张回执也只登记一次。
        """
        receipt_id = str(payload.get("receipt_id") or payload.get("id") or "").strip()
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            exists = db.execute(
                "SELECT * FROM wo_receipts WHERE board_id=? AND receipt_id=?",
                (board_id, receipt_id),
            ).fetchone()
            if exists:
                return dict(exists), False
            seq_row = db.execute(
                "SELECT COALESCE(MAX(seq),0)+1 AS next_seq FROM wo_receipts WHERE board_id=?",
                (board_id,),
            ).fetchone()
            seq = seq_row["next_seq"]
            now = datetime.now(timezone.utc).isoformat()
            cursor = db.execute(
                "INSERT INTO wo_receipts(board_id,receipt_id,seq,payload,created_at) "
                "VALUES(?,?,?,?,?) ON CONFLICT(board_id, receipt_id) DO NOTHING",
                (board_id, receipt_id, seq, json.dumps(payload, ensure_ascii=False), now),
            )
            if cursor.rowcount == 0:  # 并发下被另一请求抢先插入
                row = db.execute(
                    "SELECT * FROM wo_receipts WHERE board_id=? AND receipt_id=?",
                    (board_id, receipt_id),
                ).fetchone()
                return dict(row), False
            row = db.execute(
                "SELECT * FROM wo_receipts WHERE board_id=? AND receipt_id=?",
                (board_id, receipt_id),
            ).fetchone()
            return dict(row), True

    def list_receipts(self, board_id: str):
        with self.connect() as db:
            rows = db.execute(
                "SELECT payload FROM wo_receipts WHERE board_id=? ORDER BY seq", (board_id,)
            ).fetchall()
        return [json.loads(row["payload"]) for row in rows]

    # ---------- replay ----------

    def ledger(self, board_id: str) -> WriteOffLedger | None:
        board = self.get_board(board_id)
        if board is None:
            return None
        orders = json.loads(board["orders_json"])
        ledger = WriteOffLedger(orders)
        ledger.apply_all(self.list_receipts(board_id))
        return ledger

    def board_result(self, board_id: str):
        ledger = self.ledger(board_id)
        return ledger.board() if ledger else None
