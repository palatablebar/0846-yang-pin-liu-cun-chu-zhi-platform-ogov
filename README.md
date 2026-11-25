# 样品留存处置台

生物样本库管理员处理留存期限与保全优先级的本地工作台。样例、规则和复核记录都在项目内，不依赖外部服务。

## 启动

```bash
docker compose up --build
```

打开 `http://127.0.0.1:${PORT:-18520}`。默认端口见 `docker-compose.yml`。

## 验证

```bash
docker compose exec -T app pytest -q
docker compose config --quiet
```

## 操作

1. 在左侧选阶段，查看采信、整理、计算、判定和对账结果。
2. 选择 `dirty` 样例观察坏行和隔离原因。
3. 换 `clean` 样例执行批次，填写复核意见后签发。
4. 下载证据 JSON，核对来源摘要、指标和签发版本。

## 交接单核销核对

打开 `http://127.0.0.1:${PORT:-18520}/writeoff`（或点左侧「交接单核销核对」），按交接单查看处置回执核销结果：随到随结、处置日冲突退回待核、更正替换全部冲突回执、晚到旧回执不翻案、处置当天留存阻断、同张回执重试幂等。

- 标准场景：`sample_data/writeoff/standard.json`（2 张交接单、12 个样品，覆盖全部规则）。
- 无状态核对：`POST /api/writeoff/verify`，传 `{"scenario":"standard"}` 或 `{"orders":[...],"receipts":[...]}`。
- 规则测试：`pytest -q tests/test_writeoff.py tests/test_writeoff_http.py`。

业务规则见 `docs/BUSINESS-RULES.md`，字段定义见 `docs/DATA-DICTIONARY.md`。
