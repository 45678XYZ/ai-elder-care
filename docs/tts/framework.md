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
        ├─ 成功：上傳 S3，API 回 presigned URL
        └─ 失敗：turn 仍 completed，reply_audio_url=null
```

## 語言與 provider 順序

| Route | 優先順序 | 限制 |
|---|---|---|
| `zh-TW` | 已核准 BreezyVoice → Polly Zhiyu Neural → Zhiyu Standard | Agent 先保證繁體；BreezyVoice 目標為台灣華語。Zhiyu 是 `cmn-CN` 相容備援，不宣稱台灣口音 |
| `hak:<六腔>` | OmniVoice → VoxHakka | VoxHakka 不支援南四縣；任何客語失敗都不得轉中文語音 |

Terraform 預設不建立遠端 TTS endpoint。中文在未核准台灣華語模型時仍可使用 Polly
相容層；客語 route 沒有已啟用且通過 gate 的 provider 時回領域錯誤，Chat 只回文字。

三個自託管 provider 使用獨立 enable／approval gate，可同時建立；每個 endpoint 固定一台且
不建立 autoscaling：OmniVoice、VoxHakka 各使用 `ml.g4dn.xlarge`，BreezyVoice 使用
`ml.g4dn.4xlarge`。Instance 建立不代表模型通過 production gate。

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
