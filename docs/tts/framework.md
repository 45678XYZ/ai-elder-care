# TTS 子系統框架

## 目標與邊界

`POST /chat` 的 `lang` 是唯一語言選擇來源；系統不從漢字內容猜測語言。
`lang=hak` 時，腔調只讀 elder profile 的 `hakka_dialect`，並在 reserve turn 時保存快照，
讓相同 `client_request_id` 接管或重播時不受 profile 後續修改影響。

TTS 採 remote-only：Lambda 不下載、載入或執行開源語音模型，只呼叫 SageMaker
real-time endpoint 或 AWS Polly。`TTS_CONFIG_JSON` 是 route、provider、模型核准與聲音參數
的唯一來源。

```text
Agent reply_text + lang + profile dialect + deadline + correlation
        │
        ▼
    TtsFacade       輸入門檻；客語必須有六腔之一
        │
        ▼
    TtsRouter       設定 route → production gate → 同語言備援鏈
        │
        ├─ SageMakerTtsProvider  OmniVoice / VoxHakka / BreezyVoice
        └─ PollyTtsProvider      Zhiyu Neural / Standard
        │
        ▼
 SynthesizedAudio | TypedTtsError
        │
        ├─ 成功：上傳 S3，再把 object key 補回 turn
        └─ 失敗：turn 仍 completed，reply_audio_status=unavailable
```

## 合成不在請求路徑上

自建模型合成一段回覆要數十秒到數分鐘，而 `POST /chat` 走 API Gateway REST，整條請求上限
29 秒。在同步路徑上這些 provider 永遠等不到、永遠 fallback，等於白建。更關鍵的是逾時不會
取消：SageMaker 不因呼叫端斷線而停止推論，容器仍會把那段合成做完，於是每個逾時的請求都在
序列化的 endpoint 上多排一份沒人收得到的工作，積壓只會愈來愈深。

因此 `/chat` 只做兩件事：問 `TtsFacade.is_available()` 這輪會不會有音訊，然後把工作送進
SQS，立刻回文字。實際合成由 `tts_worker` Lambda 完成（可跑 15 分鐘），寫入 S3 之後再把
object key 補回 turn。

```text
POST /chat ──→ is_available? ──→ SQS ──→ tts_worker ──→ S3 物件
     │                                        │
     └─ 立刻回文字 + reply_audio_status        └─ 成功後才把 key 寫進 turn
```

key 必須等物件真的存在才寫進 turn：turn 帶著 key 就代表「這句話有音檔」，之後每次重播都會
依它簽發 presigned URL，指向不存在的物件時長者只會拿到一條播不出來的連結。App 端的狀態
契約見 [`docs/api.md`](../api.md) 的 `reply_audio_status`。

Chat 與 worker 的 SageMaker read timeout 刻意不同（`TTS_SAGEMAKER_READ_TIMEOUT_SECONDS`）：
chat 端短逾時讓不可能及時回應的 provider 快速失敗，worker 端則真的等它做完。

## 語言與 provider 順序

| Route | 優先順序 | 限制 |
|---|---|---|
| `zh-TW` | 已核准 BreezyVoice → Polly Zhiyu Neural → Zhiyu Standard | Agent 先保證繁體；BreezyVoice 目標為台灣華語。Zhiyu 是 `cmn-CN` 相容備援，不宣稱台灣口音 |
| `hak:<六腔>` | OmniVoice → VoxHakka | VoxHakka 不支援南四縣；任何客語失敗都不得轉中文語音 |

Terraform 預設不建立遠端 TTS endpoint。中文在未核准台灣華語模型時仍可使用 Polly
相容層；客語 route 沒有已啟用且通過 gate 的 provider 時回領域錯誤，Chat 只回文字。

三個自託管 provider 使用獨立 enable／approval gate，可同時建立；每個 endpoint 固定一台且
不建立 autoscaling：OmniVoice、VoxHakka 各使用 `ml.g4dn.xlarge`，BreezyVoice 使用
`ml.g5.4xlarge`。BreezyVoice 用 A10G 而非 T4，是因為實測 `ml.g4dn.4xlarge` 上每段文字要
20-25 秒；換機型約快 2-3 倍，但仍遠超過同步請求能等的長度，因此是非同步路徑的補強而不是
替代。Instance 建立不代表模型通過 production gate。

## 六腔設定

固定 wire values：`htia_sixian`、`htia_hailu`、`htia_dapu`、`htia_raoping`、
`htia_zhaoan`、`htia_nansixian`。`PATCH /elders/{elder_id}` 更新
`lang_preference` 與 `hakka_dialect`；下一個新 turn 的 ASR 與 TTS 同時採用。

## Agent 與 App

- 對話大腦是 AgentCore Runtime（`invoke_agent_runtime`）。Chat Lambda 在 payload 明確帶
  `lang`，大腦的 turn prefix 依此決定回覆語言，禁止從文字內容自動偵測。腔調（`hakka_dialect`）
  只影響 ASR 與 TTS 的 route，不進大腦 payload。
- `zh-TW` 回覆必須是繁體中文、台灣慣用詞；`hak` 回覆是客語漢字。
- Flutter contract 將 `reply_audio_url` 視為 nullable。無 URL 時 UI 可顯示文字並提供重試；
  裝置 TTS 只有確認裝置支援要求 locale 時才可啟用。本次不實作畫面或 AudioService。

## 同步延遲與失敗語意

TTS 維持在同步 `/chat`，單次預算上限八秒，並保留兩秒給 S3 與 DynamoDB commit。
staging 驗收目標為 TTS P95 ≤ 8 秒。所有 provider 失敗、回傳空音訊或逾時時：

- 不把 Hakka 文字交給中文 voice；
- 不讓已完成的 Agent 回覆與 routine side effect 變成 500；
- commit completed turn，`ai_respond_audio_s3_key` 缺省，API 回 `reply_audio_url=null`；
- 日誌只記 category、provider/correlation 等 allowlist 欄位，不記 reply text 或音訊。
