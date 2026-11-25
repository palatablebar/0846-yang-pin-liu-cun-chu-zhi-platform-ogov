from app.domain.helpers import D


def decide_rows(items):
    for item in items:
        if item["problem"]:
            item["verdict"] = "REJECT"
            item["reason"] = item["problem"]
        elif item["state"] == "DUE_OVERDUE":
            item["verdict"] = "REVIEW"
            item["reason"] = "DUE_OVERDUE"
        else:
            item["verdict"] = "PASS"
            item["reason"] = item["state"]
        item.setdefault("verdict", "PASS")
        item.setdefault("reason", "OK")
    return items
