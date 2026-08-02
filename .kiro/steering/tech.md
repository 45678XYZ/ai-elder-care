---
inclusion: always
---

# 技術棧與鐵則

## 用什麼

| 層 | 技術 |
|---|---|
| 前端 | Flutter（Dart SDK `>=3.4.0 <4.0.0`）、GoRouter、`flutter_lints`。目標裝置是 Android 手機，`web` build 只服務預覽與截圖 |
| 後端 | Python `>=3.11`，部署到 AWS Lambda。Pydantic 資料模型、boto3 |
| 對話大腦 | LangGraph 狀態機 + `langchain_aws`，部署到 Bedrock AgentCore Runtime（非 Lambda） |
| 基礎設施 | Terraform `>= 1.8`、AWS provider `~> 6.24`，state 存 S3。Region `us-west-2` |

## 怎麼跑

後端：

```bash
cd backend
python -m venv .venv
.venv\Scripts\activate          # macOS/Linux: source .venv/bin/activate
pip install -e ".[dev]"
python -m pytest
```

AgentCore Runtime 另需 `pip install -r agentcore_requirements.txt`。

App：

```bash
cd app
flutter pub get
flutter run
```

不帶任何 `--dart-define` 時跑 demo 假資料，不需要後端也能點完所有畫面（demo 驗證碼固定 `123456`）。

Terraform：

```bash
cd terraform
terraform init && terraform plan && terraform apply
```

**不要跑 `flutter create`。** `app/android/`、`app/web/` 已在版控內，模板會覆蓋掉麥克風權限、通知 receiver 與 `<queries>`——這些掉了不會報錯，只會變成按了沒反應。

### 提交前檢查

| 動到哪 | 跑什麼 |
|---|---|
| `backend/` | `python -m pytest` |
| `app/` | `dart format .`、`flutter analyze`（不得有 error）、`flutter test` |
| `terraform/` | `terraform fmt` |

### 接真後端

四個 `--dart-define`，值來自 `terraform output`：

```bash
flutter run \
  --dart-define=API_BASE_URL=https://xxxxxxxxxx.execute-api.us-west-2.amazonaws.com/v1 \
  --dart-define=USE_BACKEND=true \
  --dart-define=COGNITO_USER_POOL_ID=us-west-2_xxxxxxxxx \
  --dart-define=COGNITO_APP_CLIENT_ID=xxxxxxxxxxxxxxxxxxxxxxxxxx
```

登入與資料是**兩個獨立開關**：

| 帶了什麼 | 登入 | 畫面資料 |
|---|---|---|
| 什麼都不帶 | demo 假帳號 | demo 假資料 |
| 只有 `COGNITO_*` | 真 Cognito | demo 假資料 |
| 全部四個 | 真 Cognito | 真後端 |

中間那格是排查用的：登入過了但畫面壞掉，就知道問題不在認證。**`USE_BACKEND=true` 一定要配 `COGNITO_*`**——真 API 每條路由都掛 Cognito authorizer，沒 token 全部 401。

App Client 沒有 secret，這四個值本來就會被打包進 App，不是機密。

## AWS 服務對照

| 服務 | 用途 |
|---|---|
| Cognito | 使用者認證與 JWT；Pre Token Generation 注入 `elder_id` claim、Post Confirmation 訂閱 SNS |
| API Gateway REST | 路由 + JWT authorizer |
| Lambda | 14 支 handler（API 入口與排程／佇列背景工作） |
| Bedrock AgentCore Runtime | 對話大腦與託管長期記憶（不自建 memories 表） |
| Bedrock（Converse / Knowledge Bases / Embeddings） | 對話與萃取模型、衛教知識檢索、dedup 語義向量 |
| DynamoDB | 資料層，7 張表 |
| SQS + DLQ | session close 後的 batch 萃取佇列 |
| EventBridge Scheduler | idle session close、nightly summary、backfill、daily digest |
| S3 | TTS 音檔、知識庫來源文件 |
| SNS + CloudWatch | 緊急警報／晚報推播、告警與 EMF 指標 |
| Transcribe / SageMaker | ASR（華語主力 Transcribe；客語六腔 Formo endpoint）與 TTS 模型 endpoint |

## 技術鐵則

違反這幾條會壞資料或壞契約，其餘細節一律看權威文件：

1. **`docs/api.md` 是前後端唯一契約。** 改 API 行為就在同一個變更裡改它，不自創欄位名。規格見 [docs/api.md](../../docs/api.md)。
2. **所有 API 回應走 `src.shared.responses`。** 不各自手刻 status code 與 error 結構。見 [docs/conventions.md](../../docs/conventions.md)。
3. **ASR/TTS remote-only，未核准一律 fail closed。** Lambda 不跑模型推論；CE/Formo 每個模型都要逐一通過 staging/runtime、授權、存取、配額與容量核准，沒核准就不准走。客語 TTS 失敗不得改用中文 voice。見 [docs/adr/asr-remote-only.md](../../docs/adr/asr-remote-only.md)、[docs/adr/tts-remote-only.md](../../docs/adr/tts-remote-only.md)、[docs/asr/framework.md](../../docs/asr/framework.md)、[docs/tts/framework.md](../../docs/tts/framework.md)。
4. **Session 狀態只能 `active` → `closing` → `closed`。** closed 後 frozen turns、counts、snapshot hash 都不可再動；batch worker 只能改 session 上明列的 batch 控制／結果欄位，不得 reopen。見 [docs/framework.md](../../docs/framework.md)。
5. **canonical key 與 `event_id` 推導方式寫入後不可變更。** 改了會讓同一事件算出不同 ID，既有事件失去冪等收斂並產生重複紀錄。
6. **events 七類與 summary `sections` 一一對應。** 新增高階類別必須同步 `sections`、`docs/api.md` 與摘要生成器；未知類別退回 `other` 並告警，不得靜默丟棄。
7. **ID 帶型別前綴**：`eld_`、`rtn_`、`evt_`、`cnv_`、`ses_`，對外照護者識別用 `cg_`。
8. **註解、docstring、文件用繁體中文；程式碼識別符、commit message、branch 名用英文。** 註解說明「為什麼」，不覆述程式碼。

## Skill 觸發

以下情境載入對應 skill，不要憑記憶硬幹：

| 情境 | Skill |
|---|---|
| 任何開發任務開始前的定位 | `developing-ai-elder-care` |
| 動到 ASR/TTS 或 chat 語音 bridge | `developing-ai-elder-care-speech` |
| 寫 boto3／botocore 程式碼 | `aws-sdk-python-usage` |
| DynamoDB schema／access pattern／GSI／成本設計或除錯 | `amazon-dynamodb` |
| Lambda／API Gateway／EventBridge／SQS 事件源開發或除錯 | `aws-serverless` |
| 接 Lambda 到 API Gateway | `connecting-lambda-to-api-gateway` |
| 接 Lambda 到 DynamoDB（IAM／Stream） | `connecting-lambda-to-dynamodb` |
| Lambda timeout 除錯 | `debugging-lambda-timeouts` |
| Secrets Manager 密鑰 | `creating-secrets-using-best-practices` |
| S3 bucket 安全設定 | `securing-s3-buckets` |
| AWS 登入／credential | `signing-in-to-aws` |
| 向量儲存（S3 Vectors） | `storing-and-querying-vectors` |
| 用 CloudWatch 排查線上故障 | `troubleshooting-application-failures` |
| Git commit | `git-commit` |

## 本機開發

不想連 AWS 時的替代路徑（Anthropic API 或 Ollama、記憶體／SQLite 假 memory）見 [docs/local_testing.md](../../docs/local_testing.md)。
