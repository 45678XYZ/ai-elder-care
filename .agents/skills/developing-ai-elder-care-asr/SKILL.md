---
name: developing-ai-elder-care-asr
description: "ASR 子系統維護指引：remote-only 架構禁則、設定來源、文件導覽、驗證命令。修改 backend/src/shared/asr/ 或 terraform/asr_models.tf 或 docs/asr/ 時啟用。"
---

# ASR 子系統 — Agent 護欄

本 skill 僅適用於 ASR 模組；修改 ASR 相關程式、Terraform 或文件前，依序閱讀以下權威文件。所有規則都必須與專案根目錄的 `AGENTS.md` 一起遵守。

## 閱讀順序

1. [`docs/asr/framework.md`](../../../docs/asr/framework.md) — 架構入口：設計原則、元件邊界、路由策略、基礎設施概覽
2. [`docs/asr/config-schema.md`](../../../docs/asr/config-schema.md) — `ASR_CONFIG_JSON` 完整 schema 與語意
3. [`docs/asr/security-and-pii.md`](../../../docs/asr/security-and-pii.md) — 安全邊界、PII 禁則、日誌限制
4. [`docs/asr/sagemaker-inference-contract.md`](../../../docs/asr/sagemaker-inference-contract.md) — SageMaker container I/O 契約
5. [`backend/src/shared/asr/README.md`](../../../backend/src/shared/asr/README.md) — 程式碼層：檔案職責、併發、測試
6. [`docs/adr/asr-remote-only.md`](../../../docs/adr/asr-remote-only.md) — 為什麼採 remote-only 的決策紀錄

## 絕對禁則

1. **Remote-only**：Lambda 不可下載、載入或執行 ASR 模型。不可新增 `torch`、`transformers`、`faster-whisper` 或任何推論框架到 Lambda 依賴。
2. **唯一設定來源**：`ASR_CONFIG_JSON` 是 Lambda 唯一的 ASR 設定來源。不可新增分散的 ASR 環境變數。
3. **Formo prompt 邊界**：Lambda 不傳送 prompt ID。Formo 方言 prompt 固定在 SageMaker container 部署設定。
4. **Fail-closed**：未核准的路由必須回 `route_not_approved`，不可回落到任何替代方案。不可新增 `ProviderKind.LOCAL_MODEL` 或 `ProviderKind.AWS_MANAGED`。
5. **不可記錄**：音訊 bytes、逐字稿、HF token、長者個資、原始 provider 回應不可出現在日誌或錯誤訊息中。

## 設定來源規則

- `ASR_CONFIG_JSON` 為空或未設定 → 使用 `default_config()`（僅 hak_mock 可用）
- `ASR_CONFIG_JSON` 解析失敗 → 拋 `ConfigParseError`，不退回預設值
- 矛盾狀態（production 但未 approved、缺 endpoint_name 等）→ fail closed

## Terraform 前置條件

`terraform/asr_models.tf` 的 `asr_enable_endpoints` 開關：
- `false`（預設）：不建立 endpoint，不產生 GPU 費用
- `true`：必須同時提供 CE image URI、CE model-data URL、Formo image URI、Formo model-data URL、artifact bucket。缺一 validation 失敗。

修改 Terraform 時不可 `terraform apply`；只做 `terraform fmt -check` 與 `terraform validate`。

## 文件同步規則

修改 ASR 時，確認以下文件之間不走鐘：

| 修改了... | 同時檢查並更新... |
|---|---|
| `config.py`（schema 變更） | `docs/asr/config-schema.md`、相關測試 |
| `remote_endpoints.py`（契約變更） | `docs/asr/sagemaker-inference-contract.md` |
| `telemetry.py`（欄位變更） | `docs/asr/security-and-pii.md` |
| `composition.py`（路由/預設行為變更） | `docs/asr/framework.md`、`backend/src/shared/asr/README.md` |
| `terraform/asr_models.tf` | `docs/asr/framework.md` 基礎設施段落 |
| 任何 ASR 架構決策 | `docs/adr/asr-remote-only.md` 或新增 ADR |

## 驗證命令

修改程式碼後必須通過：

```powershell
cd backend
python -m pip install -e ".[dev]"
python -m pytest tests/asr -q
python -m pytest tests/asr/test_chat_asr_bridge.py -q
```

修改 Terraform 後：

```powershell
cd terraform
terraform fmt -check
terraform validate
```

## 不可做的事

- 不可刪除 `asr-lambda/environment.yml`（容器開發用途）
- 不可修改 `docs/api.md` 的 ASR 相關部分（公開 API 契約不變）
- 不可在 `pyproject.toml` 加入模型推論依賴
- 不可建立新的 `ProviderKind` enum 值（除非有完整 ADR 支持）
- 不可執行 `terraform apply` 或 `terraform destroy`
