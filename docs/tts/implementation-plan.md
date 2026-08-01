# TTS 雙語語音模組與六腔同步整合計畫

## 實作狀態（2026-07-31）

程式、契約、skill、文件與 Terraform 骨架已完成；遠端模型 endpoint 預設關閉，且三個
remote provider 的 production gate 預設未核准。Backend 全套測試與 OpenTofu validate
已通過；本機沒有 Flutter/Dart CLI，因此 Flutter contract tests 尚待有 SDK 的環境執行。
OpenTofu 驗證後 `.terraform.lock.hcl` 已恢復為 Terraform registry/hash。

## 已確認決策

- 分支：`feature/tts`，基於 `feature/asr-lambda`；未經授權不 commit、push、merge 或 apply。
- `POST /chat.lang` 維持 `zh-TW | hak` 唯一語言來源，不從漢字內容自動猜測。
- `hak` 的腔調來自 elder profile；API 固定六腔，ASR 與 TTS 使用同一份設定。
- Agent 明確依 `lang + hakka_dialect` 產生繁體中文或指定腔調的客語漢字。
- TTS 與現有 Bedrock Agents Classic 解耦串接；本案不遷移 AgentCore Runtime。
- 客語模型採 OmniVoice 主力、VoxHakka 選配備援；模型只在 SageMaker 執行。
- 中文優先台灣華語口音；Polly Zhiyu Neural／Standard 可用但只視為可切換的相容／備援 provider，
  不宣稱是台灣口音。若台灣華語模型未核准，允許設定選用 Zhiyu。
- TTS 全數失敗時保留文字 turn並回 `reply_audio_url=null`；不可用中文語音錯念客語。
- Flutter 本案只完成 API/DTO 契約與測試，不實作對話畫面或播放迴圈。
- 模型核准證據由 staging/runtime、授權、容量與延遲驗證提供。

## 實作範圍

1. 將 `backend/src/shared/tts.py` 重構成 `backend/src/shared/tts/` 領域套件，加入 facade、
   typed errors、設定 parser、provider registry、語言／腔調路由、同語言 failover、deadline 與安全遙測。
2. 中文 provider registry 支援台灣華語 remote model、Polly Zhiyu Neural 與 Standard；
   客語支援 OmniVoice 與 VoxHakka，南四縣不使用 VoxHakka。
3. 擴充 elder schema/API 的 `hakka_dialect`，新 turn reserve 時保存腔調快照；Chat 將授權後的
   語言／腔調傳入 ASR、Agent 與 TTS。
4. 將 Formo ASR 改為六個部署期固定 prompt 的 endpoint；Lambda 依 profile 選 endpoint，
   仍不傳 prompt ID。CE 保留為通用 fallback。
5. 新增預設關閉的 TTS SageMaker、最小 IAM、`TTS_CONFIG_JSON` 與 Terraform fail-closed validation。
6. 建立 TTS framework、config schema、model catalog、container contract、PII、安全 ADR、領域 README
   與專案 skill，並同步根 README、整體 framework、API、PII、user journey 與 backend README。
7. OmniVoice、VoxHakka、BreezyVoice 使用獨立 endpoint enable／approval gate，允許同時建立；
   前兩者各固定一台 `ml.g4dn.xlarge`，BreezyVoice 固定一台 `ml.g4dn.4xlarge`，不建立 autoscaling。

## 驗收

- 六腔 elder profile 能精確控制 ASR endpoint 與 TTS dialect，重送沿用原 turn 快照。
- 中文文字與 Agent 回覆皆為繁體；可由設定切換台灣華語 remote provider／Zhiyu engine。
- 客語 route 永不進入中文 provider；VoxHakka 永不服務南四縣。
- Remote provider 回應標準 MP3，音訊可上傳 S3 並由既有 presigned URL 契約取得。
- 同步 `/chat` 維持 28 秒 Lambda 上限；TTS staging P95 目標不超過 8 秒。
- 日誌不含文字、音訊、elder ID、token、endpoint、原始回應或原始例外。
- Backend、ASR、TTS、Flutter contract、skill 與 Terraform 驗證全部通過；不執行真實部署。

## 模型與授權

- `formospeech/omnivoice-hakka-community-1`：六腔客語 TTS，gated、CC BY-NC 4.0。
- `formospeech/yourtts-htia-240704`：VoxHakka TTS，五腔、CC BY-NC 4.0。
- `MediaTek-Research/BreezyVoice`：台灣華語候選，Apache 2.0；未完成 runtime/品質驗證前保持未核准。
- Polly `Zhiyu`：AWS 普通話 `cmn-CN`，支援 Neural／Standard；可用但不是台灣口音。

本專案目前採非商業原型範圍；任何商業化部署前必須重新審查客語模型授權。
