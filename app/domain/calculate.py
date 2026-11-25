from datetime import date, timedelta
from app.catalog import SPEC
from app.domain.helpers import *  # noqa: F401,F403


def calculate_rows(items):
    out = []
    rows = items
    as_of = date(2026, 11, 24)
    for item in items:
        due = add_months(item["collected_at"], item["retention_months"])
        if item["consent_until"] and item["consent_until"] < due:
            due = item["consent_until"]
        grace_end = due + __import__("datetime").timedelta(days=30)
        problem = None
        if item["disposed_at"]:
            if item["legal_hold"]:
                problem = "HOLD_VIOLATION"
            elif item["disposed_at"] < grace_end:
                problem = "EARLY_DISPOSAL"
            state = "COMPLETE"
        elif item["legal_hold"]:
            state = "RETAIN"
        elif as_of > grace_end:
            state = "DUE_OVERDUE"
        else:
            state = "RETAIN"
        item["due"] = due
        item["grace_end"] = grace_end
        item["state"] = state
        item["problem"] = problem
        item["metric"] = D(str(item["retention_months"]), "CALC")
        item["limit"] = D("24", "LIMIT")
        out.append(item)
    for item in out:
        item.setdefault("label", str(item.get(SPEC["key"], "")))
    return out
