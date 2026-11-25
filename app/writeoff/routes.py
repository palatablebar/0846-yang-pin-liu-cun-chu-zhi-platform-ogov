"""交接单核销核对 HTTP 接口与工作台页面。"""

from __future__ import annotations

from flask import Blueprint, current_app, jsonify, render_template, request

from app.errors import DomainIssue
from app.writeoff.engine import WriteOffLedger, reconcile
from app.writeoff.service import list_scenarios, load_scenario, scenario_board

writeoff_api = Blueprint("writeoff_api", __name__)


def store():
    return current_app.extensions["writeoff"]


def _json():
    return request.get_json(silent=True) or {}


def _board_or_404(board_id):
    board = store().get_board(board_id)
    if board is None:
        raise DomainIssue("WO_BOARD_NOT_FOUND", 404)
    return board


# ---------- 页面 ----------

@writeoff_api.get("/writeoff")
def page():
    initial = scenario_board("standard")
    data, _ = load_scenario("standard")
    scenarios = list_scenarios()
    arrivals = (data or {}).get("arrivals") or (data or {}).get("receipts") or []
    return render_template("writeoff.html", initial=initial, arrivals=arrivals,
                           scenarios=scenarios)


# ---------- 场景样例 ----------

@writeoff_api.get("/api/writeoff/scenarios")
def scenarios():
    return jsonify({"scenarios": list_scenarios()})


@writeoff_api.get("/api/writeoff/scenarios/<name>")
def scenario_detail(name):
    data, digest = load_scenario(name)
    if data is None:
        raise DomainIssue("SAMPLE_UNKNOWN", 404)
    return jsonify({"scenario": name, "sha256": digest, "data": data})


@writeoff_api.get("/api/writeoff/board")
def board_by_scenario():
    name = request.args.get("scenario", "standard")
    result = scenario_board(name)
    if result is None:
        raise DomainIssue("SAMPLE_UNKNOWN", 404)
    return jsonify(result)


# ---------- 无状态核对 ----------

@writeoff_api.post("/api/writeoff/verify")
def verify_route():
    body = _json()
    through = body.get("through", body.get("limit"))
    if body.get("scenario") and not body.get("orders"):
        data, _ = load_scenario(body["scenario"])
        if data is None:
            raise DomainIssue("SAMPLE_UNKNOWN", 404)
        arrivals = data.get("arrivals") or data.get("receipts") or []
        if through is not None:
            try:
                arrivals = arrivals[: max(0, int(through))]
            except (TypeError, ValueError):
                raise DomainIssue("BAD_REQUEST")
        orders = data.get("orders")
        title = data.get("title", body["scenario"])
    else:
        orders = body.get("orders") or body.get("handovers") or body.get("handover_orders")
        arrivals = body.get("receipts") or body.get("events") or body.get("records") or body.get("arrivals")
        title = None
        if orders is None:
            raise DomainIssue("BAD_REQUEST")
    try:
        board = reconcile({"orders": orders, "receipts": arrivals})
    except ValueError as exc:
        raise DomainIssue("BAD_REQUEST", 400, detail=str(exc))
    return jsonify({"ok": True, "title": title, "board": board})


# 语义化路径别名
writeoff_api.add_url_rule("/api/writeoff/check", view_func=verify_route, methods=["POST"])
writeoff_api.add_url_rule("/api/writeoff/reconcile", view_func=verify_route, methods=["POST"])


# ---------- 持久化交互台账 ----------

@writeoff_api.post("/api/writeoff/boards")
def create_board_route():
    body = _json()
    board_id = str(body.get("board_id") or body.get("id") or "default")
    title = body.get("title", "")
    orders = body.get("orders") or body.get("handovers")
    seed = body.get("receipts") or body.get("arrivals")
    if body.get("scenario"):
        data, _ = load_scenario(body["scenario"])
        if data is None:
            raise DomainIssue("SAMPLE_UNKNOWN", 404)
        orders = data.get("orders")
        title = title or data.get("title", body["scenario"])
        if body.get("seed_arrivals"):
            seed = data.get("arrivals") or data.get("receipts")
    if orders is None:
        # 默认载入标准场景的交接单
        data, _ = load_scenario("standard")
        if data is None:
            raise DomainIssue("BAD_REQUEST")
        orders = data.get("orders")
        title = title or data.get("title", "standard")
    board = store().create_board(board_id, orders, title)
    if seed:
        for item in seed:
            store().add_receipt(board_id, item)
    return jsonify(_serialize_board(board_id, board)), 201


@writeoff_api.get("/api/writeoff/boards")
def list_boards_route():
    return jsonify({"boards": store().list_boards()})


@writeoff_api.get("/api/writeoff/boards/<board_id>")
def get_board_route(board_id):
    board = _board_or_404(board_id)
    return jsonify(_serialize_board(board_id, board))


@writeoff_api.post("/api/writeoff/boards/<board_id>/receipts")
def add_receipt_route(board_id):
    _board_or_404(board_id)
    body = _json()
    payload = body.get("receipt") if isinstance(body.get("receipt"), dict) else body
    if not payload.get("receipt_id") and not payload.get("id"):
        raise DomainIssue("BAD_REQUEST")
    row, created = store().add_receipt(board_id, payload)
    ledger = store().ledger(board_id)
    board_result = ledger.board()
    sample_id = str(payload.get("sample_id") or payload.get("sid") or "").strip() or None
    sample = ledger.sample_result(sample_id) if sample_id else None
    receipt_view = None
    if sample:
        receipt_view = next((r for r in sample["receipts"]
                             if r["receipt_id"] == row["receipt_id"]), None)
    same_payload = True
    if not created:
        rec = ledger.receipts.get(row["receipt_id"])
        if rec is not None:
            same_payload = WriteOffLedger._same_business_payload(  # noqa: SLF001
                rec, payload)
    return jsonify({
        "duplicate": not created,
        "replayed": not created,
        "registered": created,
        "same_payload": same_payload,
        "ignored": bool(receipt_view and receipt_view.get("status") == "superseded"),
        "superseded": bool(receipt_view and receipt_view.get("status") == "superseded"),
        "receipt_id": row["receipt_id"],
        "seq": row["seq"],
        "receipt": receipt_view,
        "sample": sample,
        "board": board_result,
    })


@writeoff_api.post("/api/writeoff/receipts")
def add_receipt_default_route():
    body = _json()
    board_id = str(body.get("board_id") or "default")
    if store().get_board(board_id) is None:
        # 自动以标准场景交接单建板
        data, _ = load_scenario("standard")
        if data is None:
            raise DomainIssue("WO_BOARD_NOT_FOUND", 404)
        store().create_board(board_id, data.get("orders"), data.get("title", "standard"))
    return add_receipt_route(board_id)


@writeoff_api.post("/api/writeoff/boards/<board_id>/reset")
def reset_board_route(board_id):
    board = _board_or_404(board_id)
    with store().connect() as db:
        db.execute("DELETE FROM wo_receipts WHERE board_id=?", (board_id,))
    return jsonify(_serialize_board(board_id, board))


def _serialize_board(board_id, board_row):
    result = store().board_result(board_id)
    return {
        "board_id": board_id,
        "title": board_row["title"] if board_row else "",
        "created_at": board_row["created_at"] if board_row else None,
        "board": result,
    }
