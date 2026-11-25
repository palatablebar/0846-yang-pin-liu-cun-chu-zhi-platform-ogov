# 字段定义

| 字段 | 类型 | 说明 |
|---|---|---|
| `sample_id` | text | 主键 |
| `study_id` | text | 业务字段 |
| `collected_at` | date | 业务字段 |
| `retention_months` | int | 业务字段 |
| `consent_until` | date_optional | 业务字段 |
| `legal_hold` | text | 业务字段 |
| `disposed_at` | date_optional | 业务字段 |
| `storage` | text | 业务字段 |

空字符串、`-`、`NA`、`null` 和 `未提供` 按空值处理。金额字段在整理层转为整数分或整数最小单位；日期按 ISO 日期解析；带时间的字段按本地时间解析。标识字段保持为文本，前导零不会被去除。
