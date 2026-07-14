# backend/ — Python Lambda

每個 handler 對應一組 API 資源，由 API Gateway（Cognito JWT authorizer）觸發；`summary_generator` 由 EventBridge Scheduler 每晚觸發。API 規格見 [docs/api.md](../docs/api.md)。

```
src/
├── handlers/     # chat / elders / summaries / events / routines / stats / summary_generator
└── shared/       # auth（token 授權）、db（DynamoDB 六表）、responses（統一回應格式）
tests/            # pytest
```

## 開發

```bash
cd backend
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
python -m pytest
```

依賴管理在 `pyproject.toml`：`dependencies` 為執行期（進 Lambda 部署包），`[dev]` 為開發期工具。handlers 以 `from src.shared import ...` 匯入，editable install 後即可解析。
