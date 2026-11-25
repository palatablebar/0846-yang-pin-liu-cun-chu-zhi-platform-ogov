"""交接单核销处置回执的业务规则测试。

对应提示词中的每一句话：
随到随结 / 不同处置日退回待核 / 同单其他样品照常 /
更正须替换全部冲突回执 / 晚到旧回执不能翻案 /
处置当天仍须留存不能销账 / 同张回执重试不重复登记。
"""

from datetime import date

import pytest

from app.reconcile.writeoff import reconcile as reconcile_alias
from app.writeoff.engine import WriteOffLedger, reconcile
from app.writeoff.service import scenario_board


def order(order_id="JD1", *samples):
    return {"order_id": order_id, "samples": list(samples)}


def sample(sid, **kw):
    item = {"sample_id": sid}
    item.update(kw)
    return item


def receipt(rid, sid, day, **kw):
    item = {"receipt_id": rid, "sample_id": sid, "disposed_at": day}
    item.update(kw)
    return item


def state_of(board, oid, sid):
    return next(s for s in board["orders_by_id"][oid]["samples"] if s["sample_id"] == sid)


# ---------- 规则 1：随到随结，不必等齐 ----------

def test_matching_receipt_settles_immediately():
    board = reconcile([order("J", sample("A", retain_through="2026-01-01"))],
                      [receipt("R1", "A", "2026-11-10")])
    s = state_of(board, "J", "A")
    assert s["state"] == "SETTLED"
    assert s["settled"] is True
    assert s["disposal_date"] == "2026-11-10"


def test_settlement_does_not_wait_for_other_samples():
    ledger = WriteOffLedger([order("J", sample("A", retain_through="2026-01-01"),
                                  sample("B", retain_through="2026-01-01"))])
    ledger.register(receipt("R1", "A", "2026-11-10"))
    board = ledger.board()
    assert state_of(board, "J", "A")["settled"] is True
    assert state_of(board, "J", "B")["state"] == "AWAITING_RECEIPT"


# ---------- 规则 2：不同处置日 -> 已结清也退回待核 ----------

def test_different_disposal_dates_revert_settled_sample_to_pending():
    ledger = WriteOffLedger([order("J", sample("A", retain_through="2026-01-01"))])
    first = ledger.register(receipt("R1", "A", "2026-11-10"))
    assert first["result"]["state"] == "SETTLED"

    ledger.register(receipt("R2", "A", "2026-11-12"))
    s = ledger.sample_result("A")
    assert s["state"] == "PENDING_VERIFICATION"
    assert s["status"] == "PENDING"
    assert s["pending"] is True
    assert s["disputed"] is True
    assert set(s["conflict_receipt_ids"]) == {"R1", "R2"}


def test_same_disposal_date_is_not_a_conflict():
    board = reconcile([order("J", sample("A", retain_through="2026-01-01"))],
                      [receipt("R1", "A", "2026-11-10"),
                       receipt("R2", "A", "2026-11-10")])
    s = state_of(board, "J", "A")
    assert s["state"] == "SETTLED"


def test_conflict_on_one_sample_keeps_other_samples_settled():
    board = reconcile([order("J",
                             sample("A", retain_through="2026-01-01"),
                             sample("B", retain_through="2026-01-01"),
                             sample("C", retain_through="2026-01-01"))],
                      [receipt("A1", "A", "2026-11-10"), receipt("A2", "A", "2026-11-11"),
                       receipt("B1", "B", "2026-11-10"),
                       receipt("C1", "C", "2026-11-12")])
    assert state_of(board, "J", "A")["pending"] is True
    assert state_of(board, "J", "B")["settled"] is True
    assert state_of(board, "J", "C")["settled"] is True


def test_conflict_does_not_leak_across_orders():
    board = reconcile([order("J1", sample("A", retain_through="2026-01-01")),
                       order("J2", sample("K", retain_through="2026-01-01"))],
                      [receipt("A1", "A", "2026-11-10"), receipt("A2", "A", "2026-11-11"),
                       receipt("K1", "K", "2026-11-10")])
    assert state_of(board, "J1", "A")["pending"] is True
    assert state_of(board, "J2", "K")["settled"] is True


# ---------- 规则 3：更正须指明全部冲突回执 ----------

def test_partial_correction_does_not_clear_dispute():
    ledger = WriteOffLedger([order("J", sample("A", retain_through="2026-01-01"))])
    ledger.apply_all([receipt("R1", "A", "2026-11-10"),
                      receipt("R2", "A", "2026-11-11")])
    result = ledger.register(receipt("C1", "A", "2026-11-10", kind="correction", replaces=["R1"]))
    assert result["effective"] is False
    assert result["missing_conflicts"] == ["R2"]
    assert ledger.sample_result("A")["pending"] is True


def test_correction_naming_all_conflicts_clears_dispute():
    board = reconcile([order("J", sample("A", retain_through="2026-01-01"))],
                      [receipt("R1", "A", "2026-11-10"),
                       receipt("R2", "A", "2026-11-11"),
                       receipt("C", "A", "2026-11-10", kind="correction", replaces=["R1", "R2"])])
    s = state_of(board, "J", "A")
    assert s["state"] == "SETTLED"
    assert s["disposal_date"] == "2026-11-10"
    statuses = {r["receipt_id"]: r["status"] for r in s["receipts"]}
    assert statuses["R1"] == "superseded"
    assert statuses["R2"] == "superseded"
    assert statuses["C"] == "effective"


def test_late_old_receipt_named_by_correction_cannot_overturn():
    ledger = WriteOffLedger([order("J", sample("A", retain_through="2026-01-01"))])
    # 更正甚至可以预先点名尚未到达的旧回执 R-OLD
    ledger.register(receipt("R1", "A", "2026-11-10"))
    correction = ledger.register(receipt(
        "C", "A", "2026-11-12", kind="correction", replaces=["R1", "R-OLD"]))
    assert correction["effective"] is True
    assert ledger.sample_result("A")["disposal_date"] == "2026-11-12"

    late = ledger.register(receipt("R-OLD", "A", "2020-01-01"))
    assert late["registered"] is True  # 留痕登记
    s = ledger.sample_result("A")
    assert s["state"] == "SETTLED"      # 但不能翻案
    assert s["disposal_date"] == "2026-11-12"


def test_unnamed_new_conflicting_receipt_reopens_verification():
    board = reconcile([order("J", sample("A", retain_through="2026-01-01"))],
                      [receipt("R1", "A", "2026-11-10"),
                       receipt("C", "A", "2026-11-10", kind="correction", replaces=["R1"]),
                       receipt("R3-NEW", "A", "2026-11-20")])
    assert state_of(board, "J", "A")["pending"] is True


def test_comma_separated_replaces_string_is_parsed():
    board = reconcile([order("J", sample("A", retain_through="2026-01-01"))],
                      [receipt("R1", "A", "2026-11-10"),
                       receipt("R2", "A", "2026-11-11"),
                       receipt("C", "A", "2026-11-10", kind="correction",
                               replaces="R1, R2")])
    assert state_of(board, "J", "A")["settled"] is True


def test_late_named_receipt_is_flagged_superseded_but_registered():
    ledger = WriteOffLedger([order("J", sample("A", retain_through="2020-01-01"))])
    ledger.register(receipt("R1", "A", "2026-11-10"))
    ledger.register(receipt("C", "A", "2026-11-10", kind="correction",
                            replaces=["R1", "R-OLD"]))
    late = ledger.register(receipt("R-OLD", "A", "2019-01-01"))
    assert late["registered"] is True
    assert late["superseded"] is True and late["ignored"] is True
    # 更正重发同样幂等
    again = ledger.register(receipt("C", "A", "2026-11-10", kind="correction",
                                    replaces=["R1", "R-OLD"]))
    assert again["duplicate"] is True
    assert ledger.board()["receipt_count"] == 3  # R1, C, R-OLD 各一张


# ---------- 规则 4：处置当天仍须留存不能销账 ----------

def test_disposal_before_retention_due_is_blocked():
    board = reconcile([order("J", sample("A", retain_through="2026-11-30"))],
                      [receipt("R", "A", "2026-11-05")])
    s = state_of(board, "J", "A")
    assert s["state"] == "BLOCKED_RETENTION"
    assert s["blocked"] is True
    assert s["settled"] is False
    assert s["block_reason"] == "RETENTION_NOT_DUE"


def test_disposal_on_retention_due_day_is_still_retained_and_blocked():
    # 边界：处置日 == 留存截止日，当天仍须留存 -> 阻断（含端点）
    board = reconcile([order("J", sample("A", retain_through="2026-11-20"))],
                      [receipt("R", "A", "2026-11-20")])
    assert state_of(board, "J", "A")["state"] == "BLOCKED_RETENTION"


def test_disposal_day_after_due_can_write_off():
    board = reconcile([order("J", sample("A", retain_through="2026-11-20"))],
                      [receipt("R", "A", "2026-11-21")])
    assert state_of(board, "J", "A")["state"] == "SETTLED"


def test_retention_derived_from_collection_and_months_with_month_end_clamp():
    # 2025-01-31 + 12 个月 -> 2026-01-31
    board = reconcile([order("J", sample("A", collected_at="2025-01-31", retention_months=12))],
                      [receipt("R", "A", "2026-01-31")])
    s = state_of(board, "J", "A")
    assert s["retention_due"] == "2026-01-31"
    assert s["state"] == "BLOCKED_RETENTION"


def test_consent_until_can_shorten_retention():
    line = sample("A", collected_at="2026-01-15", retention_months=24, consent_until="2026-06-30")
    blocked = reconcile([order("J", line)], [receipt("R", "A", "2026-06-30")])
    assert state_of(blocked, "J", "A")["state"] == "BLOCKED_RETENTION"
    ok = reconcile([order("J", line)], [receipt("R2", "A", "2026-07-01")])
    assert state_of(ok, "J", "A")["state"] == "SETTLED"


def test_legal_hold_blocks_writeoff_even_long_after_due():
    board = reconcile([order("J", sample("A", retain_through="2020-01-01", legal_hold="Y"))],
                      [receipt("R", "A", "2026-11-18")])
    s = state_of(board, "J", "A")
    assert s["state"] == "BLOCKED_HOLD"
    assert s["block_reason"] == "LEGAL_HOLD"


def test_explicit_retain_on_disposal_day_flag_blocks():
    board = reconcile([order("J", sample("A", retain_on_disposal_day=True))],
                      [receipt("R", "A", "2026-11-18")])
    assert state_of(board, "J", "A")["state"] == "BLOCKED_RETENTION"


# ---------- 规则 5：同张回执重试幂等 ----------

def test_same_receipt_retry_registers_once():
    ledger = WriteOffLedger([order("J", sample("A", retain_through="2026-01-01"))])
    first = ledger.register(receipt("R1", "A", "2026-11-10"))
    second = ledger.register(receipt("R1", "A", "2026-11-10"))
    assert first["duplicate"] is False and first["registered"] is True
    assert second["duplicate"] is True and second["registered"] is False
    assert len(ledger.receipts) == 1
    assert ledger.board()["receipt_count"] == 1


def test_retry_does_not_change_settlement_or_double_count():
    board = reconcile([order("J", sample("A", retain_through="2026-01-01"))],
                      [receipt("R1", "A", "2026-11-10")] * 3)
    s = state_of(board, "J", "A")
    assert s["settled"] is True
    assert board["receipt_count"] == 1


# ---------- 匹配 / 异常 ----------

def test_orphan_receipt_is_reported_not_settled():
    board = reconcile([order("J", sample("A"))], [receipt("R", "GHOST", "2026-11-10")])
    assert board["exceptions"][0]["code"] == "ORPHAN_RECEIPT"
    assert state_of(board, "J", "A")["state"] == "AWAITING_RECEIPT"


def test_order_sample_mismatch_is_reported():
    board = reconcile([order("J1", sample("A")), order("J2", sample("B"))],
                      [receipt("R", "A", "2026-11-10", order_id="J2")])
    assert board["exceptions"][0]["code"] == "ORDER_SAMPLE_MISMATCH"


# ---------- 入参形态 / 别名 ----------

def test_reconcile_accepts_orders_and_receipts_positional_lists():
    board = reconcile([order("J", sample("A", retain_through="2026-01-01"))],
                      [receipt("R", "A", "2026-11-10")])
    assert board["totals"]["settled"] == 1


def test_reconcile_alias_module_returns_same_result():
    payload = {"orders": [order("J", sample("A", retain_through="2026-01-01"))],
               "receipts": [receipt("R", "A", "2026-11-10")]}
    assert reconcile_alias(payload)["totals"] == reconcile(payload)["totals"]


def test_fullwidth_date_is_canonicalized():
    board = reconcile([order("J", sample("A", retain_through="２０２６－０１－０１"))],
                      [receipt("R", "A", "２０２６－１１－１０")])
    assert state_of(board, "J", "A")["settled"] is True


# ---------- 标准场景总核对（回归 oracle） ----------

def test_standard_scenario_board_oracle():
    result = scenario_board("standard")
    board = result["board"]
    t = board["totals"]
    assert {"orders": t["orders"], "samples": t["samples"], "settled": t["settled"],
            "pending": t["pending"], "blocked": t["blocked"], "waiting": t["waiting"],
            "disputed": t["disputed"]} == {
        "orders": 2, "samples": 12, "settled": 6, "pending": 1,
        "blocked": 3, "waiting": 2, "disputed": 1}
    assert board["receipt_count"] == 17
    assert board["exceptions"][0]["code"] == "ORPHAN_RECEIPT"

    j1 = board["orders_by_id"]["JD-2026-11"]
    assert j1["counts"]["settled"] == 5
    by_id = {s["sample_id"]: s for s in j1["samples"]}
    assert by_id["YP-A02"]["settled"] is True       # 更正后结清
    assert by_id["YP-A07"]["pending"] is True       # 未更正冲突保留待核
    assert by_id["YP-A04B"]["blocked"] is True      # 截止日边界
    assert by_id["YP-A05"]["awaiting"] is True
    # 晚到旧回执 HS-A06-OLD 被替换
    a06 = by_id["YP-A06"]
    old = next(r for r in a06["receipts"] if r["receipt_id"] == "HS-A06-OLD")
    assert old["status"] == "superseded"
