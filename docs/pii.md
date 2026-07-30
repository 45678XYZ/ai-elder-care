# PII 保護說明

- **認證與授權**：Cognito 帳號；呼叫者身分一律取自 JWT token，越權回 403（見 api.md 共通慣例）
- **加密**：傳輸 HTTPS；DynamoDB／S3 靜態加密
- **同意與保留**：首次啟動同意頁與資料保留政策
- **資料**：全部使用模擬 persona，不含真實個資
- **ASR 特有規則**：音訊生命週期、逐字稿不可記錄、遙測 allowlist 等 ASR 子系統的安全邊界見 [`docs/asr/security-and-pii.md`](asr/security-and-pii.md)

TODO: 各項的具體說明與畫面。
