# TTS SageMaker inference contract

所有自託管 TTS 模型對 Lambda 暴露相同的 real-time endpoint 契約。

Request：

- `Content-Type: application/json`
- `Accept: audio/mpeg`
- UTF-8 JSON：`text`、`language`、`format="mp3"`；客語另有 `dialect`；provider 設定有
  speaker 時才加入 `speaker`。
- 不含 elder ID、session ID、correlation ID、token 或任意 model class/path。

客語例：

```json
{
  "text": "食飽吂？",
  "language": "hak",
  "dialect": "htia_hailu",
  "format": "mp3",
  "speaker": "XF"
}
```

成功 response 必須是非空 MP3 bytes；Content-Type 為 `audio/mpeg`。錯誤使用非 2xx，
不得把 traceback、模型路徑、文字或聲音內容寫進 body／log。Lambda 對空 body、超過
10 MiB、SDK 例外與逾時一律轉成安全的 typed error。

Container 必須在啟動時固定 model ID/revision、支援語言／腔調與 default speaker；不得依
request 下載另一模型。OmniVoice 的 voice-design 或 provider default 與 VoxHakka 的 `XF`
屬部署設定，App 不得指定 speaker。
