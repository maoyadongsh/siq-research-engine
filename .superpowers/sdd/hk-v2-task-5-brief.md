## Task 5: API 命令层注入 HK 数据库和 Milvus 默认 collection

**Files:**
- `apps/api/services/market_report_settings.py`
- `apps/api/services/market_report_commands.py`
- `apps/api/tests/test_market_report_commands.py`
- `apps/api/tests/test_market_report_settings.py`

**Behavior:**
从 `/parse-hk` 触发 PostgreSQL 导入时，如果用户没有传 `database_url`，API 给 HK importer 注入 `SIQ_HK_PGDATABASE=siq_hk`。Milvus dry run/ingest 若未指定 collection，HK 使用 `siq_hk_reports`。

**TDD Steps:**

- [ ] 在 `test_market_report_settings.py` 断言 `MARKET_DATABASES["HK"] == "siq_hk"`，`MARKET_VECTOR_COLLECTIONS["HK"] == "siq_hk_reports"`。
- [ ] 在 `test_market_report_commands.py` 新增 `test_market_package_import_env_defaults_hk_database`，断言 HK import plan/env 包含 `SIQ_HK_PGDATABASE=siq_hk`，US 不受影响。
- [ ] 新增 `test_market_vector_ingest_args_defaults_hk_collection`，HK payload 不传 collection 时 args 含 `--collection siq_hk_reports`。
- [ ] 运行测试，确认先失败。

**Implementation Steps:**

- [ ] 在 `market_report_settings.py` 新增 `MARKET_DATABASES` 和 `MARKET_VECTOR_COLLECTIONS`，默认值分别包含 `HK: siq_hk` 和 `HK: siq_hk_reports`。
- [ ] 在 `market_report_commands.py` 增加纯函数 `market_package_import_env(market, market_databases, base_env=None)`，返回可传给 runner 的 env overlay。
- [ ] 如果当前 command runner 没有 env overlay 能力，则在调用处传入 `env={**os.environ, **market_package_import_env(market, market_databases, base_env)}`，不要把数据库写死在命令行参数中。
- [ ] 更新 `market_vector_ingest_args()`：payload 未传 `collection` 时按市场默认 collection 注入；保留用户显式传入的 collection 优先级。

**Verification:**

```bash
cd /home/maoyd/siq-research-engine/apps/api
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -q -p no:cacheprovider tests/test_market_report_settings.py tests/test_market_report_commands.py
```

Expected: 命令层测试通过，HK 默认导入库与 collection 可由环境覆盖。

---

