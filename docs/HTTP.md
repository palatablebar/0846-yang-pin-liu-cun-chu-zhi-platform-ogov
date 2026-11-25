# 接口

- `GET /`：工作台。
- `GET /api/catalog`：阶段、字段和样例。
- `POST /api/stages/{stage}`：按阶段返回处理结果。
- `POST /api/runs`：执行完整批次并保存草稿。
- `GET /api/runs/{id}`：读取批次。
- `POST /api/runs/{id}/review`：签发或暂缓，提交 `decision`、`comment`、`expected_revision`。
- `GET /api/runs/{id}/evidence`：下载已签发证据。
- `GET /api/examples/{profile}`：下载样例。
- `GET /healthz`：健康检查。

## 交接单核销核对

- `GET /writeoff`：按交接单核对核销结果的工作台页面（首屏直出标准场景最终结果）。
- `GET /api/writeoff/scenarios`：核销场景列表。
- `GET /api/writeoff/scenarios/{name}`：场景交接单与按序到达的回执。
- `GET /api/writeoff/board?scenario=standard`：场景回执全量重放后的按单核对结果。
- `POST /api/writeoff/verify`：无状态核对。body 可为 `{"scenario":"standard","through":N}`（只重放前 N 张，用于逐步演示），或 `{"orders":[...],"receipts":[...]}`；别名 `/api/writeoff/check`、`/api/writeoff/reconcile`。
- `POST /api/writeoff/boards`：建立持久交互台账（`board_id`、`scenario` 或 `orders`，可选 `seed_arrivals`）。
- `GET /api/writeoff/boards` / `GET /api/writeoff/boards/{id}`：列出 / 读取台账核对结果。
- `POST /api/writeoff/boards/{id}/receipts`：登记回执或更正，同 `receipt_id` 重试返回 `duplicate:true` 且不重复入账。
- `POST /api/writeoff/receipts`：向 body 指定的 `board_id`（缺省 `default`，自动载入标准交接单）登记。
- `POST /api/writeoff/boards/{id}/reset`：清空该台账已登记回执。

纯 Python 核对入口：`app.writeoff.engine.reconcile({"orders": ..., "receipts": ...})`（亦可用 `WriteOffLedger`，并在 `app.reconcile.writeoff` 下有同名再导出）。
