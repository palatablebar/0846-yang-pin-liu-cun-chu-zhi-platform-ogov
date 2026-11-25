"""交接单核销核对的 HTTP / 工作台页面测试。"""


def good_receipt(**over):
    payload = {"board_id": "b1", "receipt_id": "R-HTTP-1",
               "order_id": "JD-2026-11", "sample_id": "YP-A05",
               "disposed_at": "2026-11-22"}
    payload.update(over)
    return payload


def test_writeoff_page_renders_server_side_result(client):
    response = client.get("/writeoff")
    assert response.status_code == 200
    body = response.data.decode()
    assert "按交接单核对核销结果" in body
    assert "JD-2026-11" in body
    assert "YP-A02" in body


def test_scenarios_catalog(client):
    data = client.get("/api/writeoff/scenarios").get_json()
    assert any(s["name"] == "standard" for s in data["scenarios"])


def test_scenario_detail_and_unknown_404(client):
    ok = client.get("/api/writeoff/scenarios/standard")
    assert ok.status_code == 200
    assert ok.get_json()["data"]["orders"]

    missing = client.get("/api/writeoff/scenarios/nope")
    assert missing.status_code == 404


def test_board_endpoint_returns_reconciliation(client):
    resp = client.get("/api/writeoff/board?scenario=standard")
    assert resp.status_code == 200
    board = resp.get_json()["board"]
    assert board["totals"]["samples"] == 12
    assert board["orders_by_id"]["JD-2026-11"]


def test_verify_stateless(client):
    resp = client.post("/api/writeoff/verify", json={
        "orders": [{"order_id": "J", "samples": [
            {"sample_id": "A", "retain_through": "2026-01-01"}]}],
        "receipts": [{"receipt_id": "R", "sample_id": "A", "disposed_at": "2026-11-10"}],
    })
    assert resp.status_code == 200
    assert resp.get_json()["board"]["totals"]["settled"] == 1


def test_verify_scenario_step_through(client):
    resp = client.post("/api/writeoff/verify", json={"scenario": "standard", "through": 4})
    assert resp.status_code == 200
    totals = resp.get_json()["board"]["totals"]
    assert totals["settled"] == 1
    assert totals["pending"] == 1


def test_verify_alias_paths(client):
    for path in ("/api/writeoff/check", "/api/writeoff/reconcile"):
        resp = client.post(path, json={"scenario": "standard"})
        assert resp.status_code == 200, path


def test_verify_requires_orders_or_scenario(client):
    resp = client.post("/api/writeoff/verify", json={})
    assert resp.status_code == 400


def test_live_board_receipt_retry_is_idempotent(client):
    created = client.post("/api/writeoff/boards", json={"board_id": "b1", "scenario": "standard"})
    assert created.status_code == 201

    first = client.post("/api/writeoff/boards/b1/receipts", json=good_receipt())
    assert first.status_code == 200
    assert first.get_json()["registered"] is True

    second = client.post("/api/writeoff/boards/b1/receipts", json=good_receipt())
    body = second.get_json()
    assert body["duplicate"] is True
    assert body["registered"] is False

    board = client.get("/api/writeoff/boards/b1").get_json()["board"]
    # A05 从待回执变为已核销，且全库只有一张 R-HTTP-1
    a05 = next(s for o in board["orders"] for s in o["samples"] if s["sample_id"] == "YP-A05")
    assert a05["state"] == "SETTLED"
    rows = client.get("/api/writeoff/boards").get_json()["boards"]
    assert any(row["board_id"] == "b1" for row in rows)


def test_default_receipt_endpoint_creates_board_and_replays(client):
    payload = good_receipt(board_id="default")
    r1 = client.post("/api/writeoff/receipts", json=payload)
    assert r1.status_code == 200
    r2 = client.post("/api/writeoff/receipts", json=payload)
    assert r2.get_json()["duplicate"] is True


def test_http_conflict_correction_and_late_receipt_flow(client):
    client.post("/api/writeoff/boards", json={"board_id": "f", "scenario": "standard"})

    def post(payload):
        return client.post("/api/writeoff/boards/f/receipts", json=payload).get_json()

    # 选一个原本待回执的样品 YP-B03（due 2027-10-31，会被留存阻断，故改用显式无留存单）
    client.post("/api/writeoff/boards", json={
        "board_id": "g",
        "orders": [{"order_id": "J", "samples": [{"sample_id": "A", "retain_through": "2020-01-01"}]}],
    })
    url = "/api/writeoff/boards/g/receipts"
    client.post(url, json={"receipt_id": "R1", "sample_id": "A", "disposed_at": "2026-11-10"})
    client.post(url, json={"receipt_id": "R2", "sample_id": "A", "disposed_at": "2026-11-11"})
    conflict = client.get("/api/writeoff/boards/g").get_json()["board"]
    a = conflict["orders"][0]["samples"][0]
    assert a["state"] == "PENDING_VERIFICATION"

    bad = client.post(url, json={"receipt_id": "C1", "kind": "correction",
                                 "sample_id": "A", "disposed_at": "2026-11-10",
                                 "replaces": ["R1"]}).get_json()
    assert bad["receipt"]["status"] == "ineffective"

    ok = client.post(url, json={"receipt_id": "C2", "kind": "correction",
                                "sample_id": "A", "disposed_at": "2026-11-10",
                                "replaces": ["R1", "R2", "R3-LATE"]}).get_json()
    assert ok["sample"]["state"] == "SETTLED"

    late = client.post(url, json={"receipt_id": "R3-LATE", "sample_id": "A",
                                  "disposed_at": "2019-01-01"}).get_json()
    assert late["duplicate"] is False
    assert late["sample"]["state"] == "SETTLED"
    assert late["sample"]["disposal_date"] == "2026-11-10"


def test_receipt_requires_id(client):
    client.post("/api/writeoff/boards", json={"board_id": "z", "scenario": "standard"})
    resp = client.post("/api/writeoff/boards/z/receipts", json={"sample_id": "YP-A01"})
    assert resp.status_code == 400


def test_unknown_board_404(client):
    assert client.get("/api/writeoff/boards/missing").status_code == 404
    assert client.post("/api/writeoff/boards/missing/receipts",
                       json={"receipt_id": "x"}).status_code == 404
