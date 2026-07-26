# backend/ — Python Lambda

每個 handler 對應一組 API 資源，由 API Gateway（Cognito JWT authorizer）觸發；`summary_generator` 由 EventBridge Scheduler 每晚觸發。API 規格見 [docs/api.md](../docs/api.md)。

```
src/
├── handlers/     # chat / elders / summaries / events / routines / stats / summary_generator / pre_token_generation（Cognito trigger）
├── extraction/   # 生活記錄（Module B）萃取 pipeline：分類體系、分塊、分類、萃取、canonical identity、去重
└── shared/       # auth（token 授權）、db（DynamoDB 六表）、responses（統一回應格式）、bedrock（模型呼叫）
tests/            # pytest
```

`extraction/` 只由 batch 相關 Lambda 使用，不進 realtime `/chat` 路徑；設計與移植步驟見 [docs/feature_events-extraction.md](../docs/feature_events-extraction.md)。

## 開發

```bash
cd backend
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
python -m pytest
```

依賴管理在 `pyproject.toml`：`dependencies` 為執行期（進 Lambda 部署包），`[dev]` 為開發期工具。handlers 以 `from src.shared import ...` 匯入，editable install 後即可解析。
