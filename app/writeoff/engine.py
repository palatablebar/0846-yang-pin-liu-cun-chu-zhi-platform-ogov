"""交接单核销核对核心：事件溯源的确定性台账。

输入交接单（含样品留存信息）与按到达顺序排列的处置回执/更正，
:class:`WriteOffLedger` 给出按交接单分组的核销结果。纯 Python、无 IO，
便于单测与无数据库核对；HTTP 层与 SQLite 存储只是其外层封装。
"""

from __future__ import annotations

from collections import OrderedDict
from datetime import date
from typing import Iterable

from app.domain.helpers import add_months
from app.writeoff.model import (
    AWAITING_RECEIPT,
    BLOCKED_HOLD,
    BLOCKED_RETENTION,
    BUCKET,
    CORRECTION,
    ORDERS_CONTAINER_KEYS,
    ORDER_SAMPLES_KEYS,
    PENDING_VERIFICATION,
    RECEIPT,
    RECEIPTS_CONTAINER_KEYS,
    REPLACES_KEYS,
    SETTLED,
    STATE_LABELS,
    as_bool,
    date_text,
    first_key,
    kind_of,
    parse_date,
    COLLECTED_KEYS,
    CONSENT_KEYS,
    DISPOSABLE_FROM_KEYS,
    HOLD_KEYS,
    ORDER_ID_KEYS,
    RECEIPT_DATE_KEYS,
    RECEIPT_ID_KEYS,
    RECEIPT_ORDER_KEYS,
    RECEIPT_SAMPLE_KEYS,
    RETAIN_FLAG_KEYS,
    RETAIN_THROUGH_KEYS,
    RETENTION_MONTHS_KEYS,
    SAMPLE_ID_KEYS,
)


class ReceiptConflict(ValueError):
    """同一回执编号以不同内容再次提交。"""


def _list_value(value) -> list:
    if value is None:
        return []
    if isinstance(value, str):
        text = value.replace("，", ",").replace("、", ",")
        return [part.strip() for part in text.split(",") if part.strip()]
    if isinstance(value, (list, tuple, set)):
        return [item for item in value]
    return [value]


def retention_boundary(line: dict) -> tuple[date | None, date | None, bool | None]:
    """返回 (retain_through 最后留存日, disposable_from 最早可处置日, 显式当天留存标记)。"""
    retain_through = parse_date(first_key(line, RETAIN_THROUGH_KEYS))
    disposable_from = parse_date(first_key(line, DISPOSABLE_FROM_KEYS))

    if retain_through is None:
        collected = parse_date(first_key(line, COLLECTED_KEYS))
        months_raw = first_key(line, RETENTION_MONTHS_KEYS)
        months = None
        if months_raw is not None:
            try:
                months = int(str(months_raw).strip())
            except (TypeError, ValueError):
                months = None
        if collected is not None and months is not None:
            retain_through = add_months(collected, months)
            consent = parse_date(first_key(line, CONSENT_KEYS))
            if consent is not None and consent < retain_through:
                retain_through = consent

    flag = None
    if first_key(line, RETAIN_FLAG_KEYS, None) is not None:
        flag = as_bool(first_key(line, RETAIN_FLAG_KEYS))
    return retain_through, disposable_from, flag


def retention_blocks(line: dict, disposal_day: date) -> tuple[bool, str | None, date | None]:
    """处置日当天是否仍须留存。返回 (阻断, 原因码, 留存截止)。"""
    retain_through, disposable_from, flag = retention_boundary(line)
    hold = as_bool(first_key(line, HOLD_KEYS, None))
    if hold:
        return True, "LEGAL_HOLD", retain_through

    if flag is True:
        return True, "RETAIN_ON_DISPOSAL_DAY", retain_through
    if flag is False:
        return False, None, retain_through

    # 给出「最早可处置日」时按其口径：早于该日不能销账
    if disposable_from is not None and disposal_day < disposable_from:
        return True, "RETENTION_NOT_DUE", retain_through
    # 留存截止日即最后一个仍须留存的日子（含当天），故 <= 即阻断
    if retain_through is not None and disposal_day <= retain_through:
        return True, "RETENTION_NOT_DUE", retain_through
    return False, None, retain_through


class WriteOffLedger:
    """按到达顺序接收回执/更正，并可随时输出按交接单分组的核对结果。"""

    def __init__(self, orders: Iterable[dict] | dict | None = None):
        self.orders: "OrderedDict[str, dict]" = OrderedDict()
        self.sample_order: dict[str, str] = {}
        self.receipts: "OrderedDict[str, dict]" = OrderedDict()
        self.events: list[dict] = []
        self.exceptions: list[dict] = []
        self.seq = 0
        if orders is not None:
            self.load_orders(orders)

    # ---------- 交接单 ----------

    def load_orders(self, orders) -> "WriteOffLedger":
        if isinstance(orders, dict) and not isinstance(orders, OrderedDict):
            # 允许 {order_id: {...}} 映射形式
            orders = [dict(value, order_id=key) if isinstance(value, dict) else value
                      for key, value in orders.items()]
        for raw in orders or []:
            self.add_order(raw)
        return self

    def add_order(self, raw_order: dict) -> str:
        order_id = str(first_key(raw_order, ORDER_ID_KEYS, "")).strip()
        if not order_id:
            raise ValueError("ORDER_ID_MISSING")
        self.orders.setdefault(order_id, {
            "order_id": order_id,
            "samples": OrderedDict(),
        })
        for raw_line in first_key(raw_order, ORDER_SAMPLES_KEYS, []) or []:
            self.add_sample(order_id, raw_line)
        return order_id

    def add_sample(self, order_id: str, raw_line: dict) -> str:
        sample_id = str(first_key(raw_line, SAMPLE_ID_KEYS, "")).strip()
        if not sample_id:
            raise ValueError("SAMPLE_ID_MISSING")
        line = dict(raw_line)
        line["sample_id"] = sample_id
        line["order_id"] = order_id
        self.orders[order_id]["samples"][sample_id] = line
        self.sample_order[sample_id] = order_id
        # 允许交接单行内联回执
        for embedded in _list_value(raw_line.get("receipts") or raw_line.get("records")):
            if isinstance(embedded, dict):
                embedded = dict(embedded)
                embedded.setdefault(RECEIPT_SAMPLE_KEYS[0], sample_id)
                embedded.setdefault(RECEIPT_ORDER_KEYS[0], order_id)
                self.register(embedded)
        if raw_line.get("receipt_id") and first_key(raw_line, RECEIPT_DATE_KEYS):
            self.register({
                "receipt_id": raw_line["receipt_id"],
                "sample_id": sample_id,
                "order_id": order_id,
                "disposed_at": first_key(raw_line, RECEIPT_DATE_KEYS),
            })
        return sample_id

    # ---------- 回执/更正登记（幂等） ----------

    def register(self, raw: dict) -> dict:
        """登记一张回执或更正。同 receipt_id 重试不重复登记。

        返回包含 ``duplicate`` 标记与当前核对结果的字典。
        """
        receipt_id = str(first_key(raw, RECEIPT_ID_KEYS, "")).strip()
        if not receipt_id:
            raise ValueError("RECEIPT_ID_MISSING")

        if receipt_id in self.receipts:
            existing = self.receipts[receipt_id]
            same = self._same_business_payload(existing, raw)
            replay = {
                "duplicate": True,
                "replayed": True,
                "registered": False,
                "receipt_id": receipt_id,
                "same_payload": same,
                "seq": existing["seq"],
            }
            if not same:
                # 同号但业务内容不同：不覆盖原记录，明确报冲突
                replay["code"] = "RECEIPT_ID_CONTENT_CONFLICT"
            self.events.append({"op": "retry", "receipt_id": receipt_id, "same_payload": same})
            replay["result"] = self.sample_result(existing["sample_id"]) if existing.get("sample_id") else None
            return replay

        kind = kind_of(raw)
        sample_id = str(first_key(raw, RECEIPT_SAMPLE_KEYS, "")).strip()
        order_id = first_key(raw, RECEIPT_ORDER_KEYS)
        order_id = str(order_id).strip() if order_id is not None else None
        day = parse_date(first_key(raw, RECEIPT_DATE_KEYS))
        replaces = [str(x).strip() for x in _list_value(first_key(raw, REPLACES_KEYS, [])) if str(x).strip()]

        self.seq += 1
        record = {
            "seq": self.seq,
            "receipt_id": receipt_id,
            "kind": kind,
            "sample_id": sample_id or None,
            "order_id": order_id or None,
            "disposal_date": day.isoformat() if day else date_text(first_key(raw, RECEIPT_DATE_KEYS)),
            "replaces": replaces,
            "effective": None,
            "supersedes_now": [],
            "missing_conflicts": [],
            "raw": raw,
        }

        matched_order, problem = self._match_order(sample_id, order_id)
        if problem:
            record["problem"] = problem
            self.exceptions.append({
                "code": problem, "receipt_id": receipt_id,
                "sample_id": sample_id or None, "order_id": order_id or None,
            })

        # 更正的有效性需要在「登记前」的冲突集合上判定
        if kind == CORRECTION and not problem and sample_id:
            conflict_ids = self._current_conflict_ids(sample_id)
            missing = [rid for rid in conflict_ids if rid not in replaces]
            record["missing_conflicts"] = missing
            if conflict_ids and missing:
                record["effective"] = False
                record["code"] = "CORRECTION_INCOMPLETE"
            else:
                record["effective"] = True
                record["supersedes_now"] = list(replaces)

        self.receipts[receipt_id] = record
        self.events.append({"op": "register", "seq": self.seq, "receipt_id": receipt_id,
                            "kind": kind, "sample_id": sample_id or None})

        tombstoned = kind == RECEIPT and receipt_id in self._tombstones()
        return {
            "duplicate": False,
            "replayed": False,
            "registered": True,
            "ignored": tombstoned,
            "superseded": tombstoned,
            "overturns_correction": False,
            "receipt_id": receipt_id,
            "seq": self.seq,
            "effective": record["effective"],
            "missing_conflicts": record["missing_conflicts"],
            "problem": record.get("problem"),
            "result": self.sample_result(sample_id) if sample_id and not problem else None,
        }

    def apply_all(self, records: Iterable[dict]) -> "WriteOffLedger":
        for raw in records or []:
            if not isinstance(raw, dict):
                continue
            # 允许 {"receipt": {...}} / {"event": ...} 信封
            inner = raw.get("receipt") or raw.get("data") or raw.get("event")
            if isinstance(inner, dict) and not first_key(raw, RECEIPT_ID_KEYS):
                envelope_kind = raw.get("kind") or raw.get("type")
                if envelope_kind and not inner.get("kind"):
                    inner = dict(inner, kind=envelope_kind)
                raw = inner
            self.register(raw)
        return self

    # ---------- 匹配与冲突 ----------

    def _match_order(self, sample_id: str, order_id: str | None) -> tuple[str | None, str | None]:
        if not sample_id:
            return None, "SAMPLE_ID_MISSING"
        known_order = self.sample_order.get(sample_id)
        if order_id:
            if order_id not in self.orders:
                return None, "ORDER_UNKNOWN"
            if known_order and known_order != order_id:
                return None, "ORDER_SAMPLE_MISMATCH"
            if sample_id not in self.orders[order_id]["samples"]:
                return None, "SAMPLE_NOT_IN_ORDER"
            return order_id, None
        if known_order:
            return known_order, None
        return None, "ORPHAN_RECEIPT"

    def _tombstones(self) -> set[str]:
        """所有生效更正指明替换的回执编号（含尚未到达的晚到回执）。"""
        hidden: set[str] = set()
        for rec in self.receipts.values():
            if rec["kind"] == CORRECTION and rec.get("effective"):
                hidden.update(rec["replaces"])
        return hidden

    def _authoritative_date(self, sample_id: str) -> str | None:
        date_value = None
        for rec in self.receipts.values():
            if (rec["kind"] == CORRECTION and rec.get("effective")
                    and rec.get("sample_id") == sample_id and rec.get("disposal_date")):
                date_value = rec["disposal_date"]  # 后到的生效更正优先
        return date_value

    def _live_receipts(self, sample_id: str) -> list[dict]:
        hidden = self._tombstones()
        out = []
        for rec in self.receipts.values():
            if rec["kind"] != RECEIPT or rec.get("sample_id") != sample_id:
                continue
            if rec["receipt_id"] in hidden:
                continue
            if rec.get("problem"):
                continue
            out.append(rec)
        return out

    def _current_conflict_ids(self, sample_id: str) -> list[str]:
        live = self._live_receipts(sample_id)
        dates = {rec["disposal_date"] for rec in live if rec["disposal_date"]}
        if len(dates) >= 2:
            return [rec["receipt_id"] for rec in live]
        return []

    @staticmethod
    def _same_business_payload(old_record: dict, new_raw: dict) -> bool:
        """比较已存记录与重提回执的关键业务内容是否一致（重试应为同张回执）。"""
        new_kind = kind_of(new_raw)
        new_sample = str(first_key(new_raw, RECEIPT_SAMPLE_KEYS, "")).strip() or None
        new_day = parse_date(first_key(new_raw, RECEIPT_DATE_KEYS))
        new_order = first_key(new_raw, RECEIPT_ORDER_KEYS)
        new_order = str(new_order).strip() if new_order is not None else None
        new_replaces = sorted(str(x).strip() for x in
                              _list_value(first_key(new_raw, REPLACES_KEYS, [])) if str(x).strip())
        return (
            old_record.get("kind") == new_kind
            and (old_record.get("sample_id") or None) == new_sample
            and (old_record.get("order_id") or None) == new_order
            and (old_record.get("disposal_date") or None) == (new_day.isoformat() if new_day else None)
            and sorted(old_record.get("replaces") or []) == new_replaces
        )

    # ---------- 结果 ----------

    def sample_result(self, sample_id: str) -> dict | None:
        order_id = self.sample_order.get(sample_id)
        if not order_id:
            return None
        line = self.orders[order_id]["samples"][sample_id]
        hidden = self._tombstones()
        live = self._live_receipts(sample_id)
        auth_date = self._authoritative_date(sample_id)

        dates = {rec["disposal_date"] for rec in live if rec["disposal_date"]}
        if auth_date:
            dates.add(auth_date)
        conflict = len(dates) >= 2

        retain_through, disposable_from, _ = retention_boundary(line)

        if conflict:
            state = PENDING_VERIFICATION
            disposal_day = None
            block_reason = None
        elif len(dates) == 1:
            day_text = next(iter(dates))
            disposal_day = parse_date(day_text)
            blocked, block_reason, retain_through = retention_blocks(line, disposal_day)
            if blocked and block_reason == "LEGAL_HOLD":
                state = BLOCKED_HOLD
            elif blocked:
                state = BLOCKED_RETENTION
            else:
                state = SETTLED
        else:
            state = AWAITING_RECEIPT
            disposal_day = None
            block_reason = None

        receipt_views = []
        for rec in self.receipts.values():
            if rec.get("sample_id") != sample_id:
                continue
            status = "recorded"
            if rec["kind"] == CORRECTION:
                status = "effective" if rec.get("effective") else (
                    "ineffective" if rec.get("effective") is False else "correction")
            elif rec["receipt_id"] in hidden:
                status = "superseded"
            receipt_views.append({
                "receipt_id": rec["receipt_id"],
                "kind": rec["kind"],
                "disposal_date": rec["disposal_date"],
                "status": status,
                "effective": rec.get("effective"),
                "replaces": rec["replaces"],
                "missing_conflicts": rec["missing_conflicts"],
                "seq": rec["seq"],
            })

        active_ids = [r["receipt_id"] for r in live]
        conflict_ids = self._current_conflict_ids(sample_id) if conflict else []
        settled = state == SETTLED
        return {
            "sample_id": sample_id,
            "order_id": order_id,
            "state": state,
            "status": BUCKET[state],
            "state_code": state,
            "status_code": BUCKET[state],
            "state_label": STATE_LABELS[state],
            "status_label": STATE_LABELS[state],
            "label": STATE_LABELS[state],
            "settled": settled,
            "written_off": settled,
            "cleared": settled,
            "pending": state == PENDING_VERIFICATION,
            "awaiting": state == AWAITING_RECEIPT,
            "blocked": state in (BLOCKED_RETENTION, BLOCKED_HOLD),
            "disputed": conflict,
            "conflict": conflict,
            "disposal_date": disposal_day.isoformat() if isinstance(disposal_day, date) else None,
            "retention_due": retain_through.isoformat() if isinstance(retain_through, date) else None,
            "retain_through": retain_through.isoformat() if isinstance(retain_through, date) else None,
            "block_reason": block_reason,
            "reason": block_reason or ("DATE_CONFLICT" if conflict else state),
            "reason_codes": self._reason_codes(state, block_reason, conflict),
            "active_receipt_ids": active_ids,
            "conflict_receipt_ids": conflict_ids,
            "receipts": receipt_views,
        }

    @staticmethod
    def _reason_codes(state, block_reason, conflict) -> list[str]:
        codes = []
        if conflict:
            codes.append("DATE_CONFLICT")
        if block_reason:
            codes.append(block_reason)
        codes.append(state)
        return codes

    def order_result(self, order_id: str) -> dict:
        order = self.orders[order_id]
        samples = [self.sample_result(sid) for sid in order["samples"]]
        counts = {"samples": len(samples), "settled": 0, "pending": 0,
                  "waiting": 0, "blocked": 0, "disputed": 0}
        for item in samples:
            if item["state"] == SETTLED:
                counts["settled"] += 1
            elif item["state"] == PENDING_VERIFICATION:
                counts["pending"] += 1
            elif item["state"] == AWAITING_RECEIPT:
                counts["waiting"] += 1
            elif item["state"] in (BLOCKED_RETENTION, BLOCKED_HOLD):
                counts["blocked"] += 1
            if item["disputed"]:
                counts["disputed"] += 1
        counts["unsettled"] = counts["samples"] - counts["settled"]
        return {
            "order_id": order_id,
            "counts": counts,
            "all_settled": counts["unsettled"] == 0 and counts["samples"] > 0,
            "settled": counts["unsettled"] == 0 and counts["samples"] > 0,
            "samples": samples,
        }

    def board(self) -> dict:
        orders = [self.order_result(oid) for oid in self.orders]
        totals = {"orders": len(orders), "samples": 0, "settled": 0, "pending": 0,
                  "waiting": 0, "blocked": 0, "disputed": 0, "exceptions": len(self.exceptions),
                  "receipts": len(self.receipts)}
        by_id = {}
        for order in orders:
            by_id[order["order_id"]] = order
            for key in ("samples", "settled", "pending", "waiting", "blocked", "disputed"):
                totals[key] += order["counts"][key]
        return {
            "orders": orders,
            "orders_by_id": by_id,
            "totals": totals,
            "counts": totals,
            "exceptions": list(self.exceptions),
            "events": list(self.events),
            "receipt_count": len(self.receipts),
        }


# ---------- 无状态函数式入口（多种常见命名，便于核对/测试调用） ----------

def build_ledger(orders=None, receipts=None) -> WriteOffLedger:
    ledger = WriteOffLedger(orders)
    ledger.apply_all(receipts)
    return ledger


def reconcile(payload=None, receipts=None, orders=None, **kwargs) -> dict:
    """对入参执行核对。

    支持：
    - ``reconcile({"orders": [...], "receipts": [...]})``
    - ``reconcile(orders_list, receipts_list)``
    - ``reconcile(orders=..., receipts=...)``
    """
    # 位置参数形态：reconcile(orders, receipts)
    if isinstance(payload, list) and (receipts is not None or orders is not None):
        ledger = build_ledger(payload, receipts if receipts is not None else orders)
        return ledger.board()
    if orders is not None:
        ledger = build_ledger(orders, receipts if receipts is not None else payload)
        return ledger.board()
    if isinstance(payload, dict):
        order_data = first_key(payload, ORDERS_CONTAINER_KEYS)
        record_data = receipts if receipts is not None else first_key(
            payload, RECEIPTS_CONTAINER_KEYS)
        ledger = build_ledger(order_data, record_data)
        return ledger.board()
    if isinstance(payload, list):
        # 单列表：元素像交接单则当作 orders，否则当作 receipts
        if payload and isinstance(payload[0], dict) and first_key(payload[0], ORDER_SAMPLES_KEYS) is not None:
            ledger = build_ledger(payload, kwargs.get("receipts"))
        else:
            ledger = build_ledger(kwargs.get("orders"), payload)
        return ledger.board()
    if payload is None:
        ledger = build_ledger(None, receipts)
        return ledger.board()
    raise TypeError("reconcile accepts a dict, orders list, or (orders, receipts)")


# 语义化别名
verify_writeoff = reconcile
check_writeoff = reconcile
write_off = reconcile
writeoff = reconcile
settle_receipts = reconcile
verify = reconcile
build_board = reconcile


class Reconciler(WriteOffLedger):
    """常见类名别名。"""


class WriteOffReconciler(WriteOffLedger):
    pass


class Ledger(WriteOffLedger):
    pass
