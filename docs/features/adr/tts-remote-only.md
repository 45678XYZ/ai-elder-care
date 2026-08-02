# ADR：TTS remote-only 與同語言備援

- 狀態：Accepted
- 日期：2026-07-31

## 決策

開源 TTS 模型只部署在 SageMaker endpoint，Chat Lambda 不載入模型。所有 route 由
`TTS_CONFIG_JSON` 控制並受 production gate 管制。語言只信任 `POST /chat.lang`；客語腔調
只信任 elder profile，reserve 時保存 turn 快照。

Fallback 必須具有同語言、同腔調能力。客語 provider 全失敗時不得用中文 voice 念客語；
Chat 仍提交文字結果並回 `reply_audio_url=null`。中文優先已核准的台灣華語 provider，
Polly Zhiyu Neural／Standard 只作 `cmn-CN` 相容鏈。

## 理由與後果

此邊界讓模型可切換、避免 Lambda 冷啟動與套件膨脹，也防止語言錯配造成對長者最明顯的
失真。代價是 endpoint 成本與同步延遲；因此預設不建立 GPU endpoint，並要求 staging
P95 ≤ 8 秒及容量、授權、存取逐項核准。
