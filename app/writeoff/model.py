"""交接单核销处置回执 —— 领域常量与输入归一化。

业务口径（见 docs/BUSINESS-RULES.md「交接单核销」）：

- 回执按交接单逐样品核销，对得上且可核销的样品随到随结；
- 同样品不同处置日构成冲突，已结清也退回待核，同单其他样品不受影响；
- 更正必须指明其所替换的「全部冲突回执」才能消除争议，被指明的旧回执
  即使晚到也按已替换回执处理，不能推翻更正；
- 处置当天仍在留存期（含留存截止日当天）或处于保全状态的样品不能销账；
- 同一张回执（同一回执编号）重试只登记一次。
"""

from __future__ import annotations

from datetime import date
from typing import Any

from app.intake.normalize import canonical

# ---- 状态码（细粒度 state 与粗粒度 status 两套，便于核对） ----

SETTLED = "SETTLED"                       # 已核销 / 已结清
AWAITING_RECEIPT = "AWAITING_RECEIPT"     # 尚无有效回执
PENDING_VERIFICATION = "PENDING_VERIFICATION"  # 日期冲突，退回待核
BLOCKED_RETENTION = "BLOCKED_RETENTION"   # 处置当天仍须留存
BLOCKED_HOLD = "BLOCKED_HOLD"             # 保全标记优先阻止处置

# 粗粒度桶
BUCKET = {
    SETTLED: "SETTLED",
    AWAITING_RECEIPT: "WAITING",
    PENDING_VERIFICATION: "PENDING",
    BLOCKED_RETENTION: "BLOCKED",
    BLOCKED_HOLD: "BLOCKED",
}

STATE_LABELS = {
    SETTLED: "已核销",
    AWAITING_RECEIPT: "待回执",
    PENDING_VERIFICATION: "待核",
    BLOCKED_RETENTION: "留存期内·不能销账",
    BLOCKED_HOLD: "保全中·不能销账",
}

# 已结清的同义中文，工作台文案/核对可能使用「结清」
SETTLED_WORDS = ("已核销", "已结清", "核销", "结清")
PENDING_WORDS = ("待核", "待核实", "待核验")

RECEIPT = "RECEIPT"
CORRECTION = "CORRECTION"

CORRECTION_WORDS = {"correction", "correct", "更正", "更正回执", "gz", "update", "amendment"}

TRUE_WORDS = {"y", "yes", "true", "1", "是", "有", "保全", "冻结", "需保全"}
FALSE_WORDS = {"n", "no", "false", "0", "否", "无"}

# ---- 字段别名：兼容不同数据口径（含中文字段名） ----

ORDER_ID_KEYS = ("order_id", "handover_id", "handover_no", "handover_order_id",
                 "handover_code", "jd_no", "交接单号", "交接单编号", "交接单id", "单号", "id")
ORDER_SAMPLES_KEYS = ("samples", "lines", "items", "details", "sample_lines",
                      "样品明细", "交接样品", "样品列表", "明细", "样品")
SAMPLE_ID_KEYS = ("sample_id", "sid", "sample_no", "code",
                  "样品编号", "样品id", "样品号", "编号", "id")
ORDER_DATE_KEYS = ("handover_date", "order_date", "created_at", "date", "交接日期")

RETAIN_THROUGH_KEYS = (
    "retain_through", "must_retain_until", "retention_end", "retention_until",
    "retain_until", "retention_due", "due_date", "due",
    "留存截止", "留存截止日", "留存到期日", "应留存至", "保留至", "留存至",
)
DISPOSABLE_FROM_KEYS = ("disposable_from", "earliest_disposal_date", "can_dispose_from",
                        "最早可处置日", "可处置日")
COLLECTED_KEYS = ("collected_at", "collected_date", "collection_date",
                  "采集日期", "采样日期", "收集日期")
RETENTION_MONTHS_KEYS = ("retention_months", "retention_month", "months",
                         "留存月数", "保存月数", "留存期限月数")
CONSENT_KEYS = ("consent_until", "consent_date", "同意截止", "知情同意截止")
HOLD_KEYS = ("legal_hold", "hold", "blocked", "preservation_hold", "under_hold", "preserve",
             "保全标记", "保全", "法律冻结", "是否保全")
RETAIN_FLAG_KEYS = ("retain_on_disposal_day", "must_retain_on_disposal_day",
                    "retention_blocks_disposal", "处置当天仍须留存", "当天需留存")

RECEIPT_ID_KEYS = ("receipt_id", "id", "receipt_no", "receipt_number", "serial", "no",
                   "回执编号", "回执号", "回执id", "编号")
RECEIPT_SAMPLE_KEYS = ("sample_id", "sid", "sample_no", "code",
                       "样品编号", "样品id", "样品号")
RECEIPT_ORDER_KEYS = ("order_id", "handover_id", "handover_no", "handover_order_id",
                      "交接单号", "交接单编号")
RECEIPT_DATE_KEYS = ("disposed_at", "disposal_date", "disposed_date", "dispose_date",
                     "disposal_day", "date", "处置日期", "处置日", "核销日期")
RECEIPT_KIND_KEYS = ("kind", "type", "record_type", "category", "类型", "记录类型", "种类")
REPLACES_KEYS = ("replaces", "replace", "supersedes", "replaced_receipts",
                 "replace_ids", "replaced_ids", "conflicts",
                 "conflicting_receipt_ids", "conflict_receipt_ids",
                 "替换回执", "所替换回执", "替换的回执", "替换回执编号", "全部冲突回执")

ORDERS_CONTAINER_KEYS = ("orders", "handovers", "handover_orders", "交接单", "交接单列表")
RECEIPTS_CONTAINER_KEYS = ("receipts", "events", "records", "arrivals", "logs",
                           "回执", "回执列表", "到达记录")


def first_key(data: Any, keys, default=None):
    """按别名顺序取第一个非空值。"""
    if not isinstance(data, dict):
        return default
    for key in keys:
        if key in data and data[key] is not None and canonical(data[key]) != "":
            return data[key]
    return default


def parse_date(value):
    """宽容解析 ISO/全角/常见斜杠日期，失败返回 None。"""
    text = canonical(value)
    if not text:
        return None
    # ２０２６－１１－２０ 全角已由 NFKC 归一；兼容 2026/11/20
    candidate = text.replace("/", "-").replace(".", "-").replace("年", "-").replace("月", "-").replace("日", "")
    try:
        return date.fromisoformat(candidate)
    except ValueError:
        pass
    for fmt in ("%Y-%m-%d", "%Y-%-m-%-d"):
        try:
            from datetime import datetime
            return datetime.strptime(candidate, fmt).date()
        except ValueError:
            continue
    return None


def as_bool(value) -> bool | None:
    """把 Y/是/1/true 等解析为布尔；无法判断返回 None。"""
    if isinstance(value, bool):
        return value
    text = canonical(value).lower()
    if text == "":
        return None
    if text in TRUE_WORDS:
        return True
    if text in FALSE_WORDS:
        return False
    return None


def kind_of(data: dict) -> str:
    text = canonical(first_key(data, RECEIPT_KIND_KEYS, "")).lower()
    if text in CORRECTION_WORDS:
        return CORRECTION
    has_replaces = first_key(data, REPLACES_KEYS, None) is not None
    # 未显式声明为回执、但带「替换列表」的记录按更正处理
    if has_replaces and text not in {"receipt", "回执"}:
        return CORRECTION
    return RECEIPT


def date_text(value) -> str | None:
    parsed = parse_date(value)
    return parsed.isoformat() if parsed else (canonical(value) or None)
