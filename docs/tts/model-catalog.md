# TTS 模型目錄與核准狀態

| 模型／服務 | 語言與腔調 | 授權／服務性質 | 部署／預設狀態 |
|---|---|---|---|
| `formospeech/omnivoice-hakka-community-1` | 客語六腔 | CC BY-NC 4.0、gated | 一台 `ml.g4dn.xlarge`、無 autoscaling；endpoint 與 production gate 關閉，客語主候選 |
| `formospeech/yourtts-htia-240704`（VoxHakka） | 四縣、海陸、大埔、饒平、詔安；預設 speaker `XF` | CC BY-NC 4.0 | 一台 `ml.g4dn.xlarge`、無 autoscaling；endpoint 與 gate 關閉，同語言備援，不支援南四縣 |
| `MediaTek-Research/BreezyVoice` | 台灣華語候選 | Apache-2.0 | 一台 `ml.g4dn.4xlarge`、無 autoscaling；endpoint 與 gate 關閉，須先驗證繁體輸入、聲線、容量與 P95 |
| AWS Polly `Zhiyu` Neural | Mandarin `cmn-CN` | AWS managed | 中文相容備援；不是台灣口音 |
| AWS Polly `Zhiyu` Standard | Mandarin `cmn-CN` | AWS managed | Neural 失敗後最後相容備援 |

OmniVoice 與 VoxHakka 的非商業授權是硬邊界：專案若轉商用，`license_cleared` 不得設為
true，除非另取得可用授權。模型頁宣告或第三方聲稱不等於 production 核准；每個模型仍須
保存 approval record，並通過 staging 音質、錯字、容量與延遲驗收。

模型切換只改 `TTS_CONFIG_JSON` route/provider 順序。更換模型可能明顯改變聲線；本階段
不做 voice cloning，也不保存長者聲紋。

三個 SageMaker providers 各有獨立 enable 與 approval gate，可全部同時建立。建立 endpoint
不等於核准；指定 instance 的 staging/runtime 品質、容量與 P95 證據完整前仍須 fail closed。
