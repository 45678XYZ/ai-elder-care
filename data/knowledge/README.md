# knowledge/ — 衛教文件

Bedrock Knowledge Base 的資料來源，部署時上傳至 S3（見 terraform/s3.tf、terraform/bedrock_kb.tf）。

僅收錄公開衛教資料；AI 回應僅供參考、不做醫療診斷。

## 內容

29 篇衛生福利部／國民健康署公開衛教文章的純文字檔，涵蓋慢性病（高血壓、糖尿病、腦中風、代謝症候群、氣喘、慢性阻塞性肺病）、失智照護、長照服務資源（喘息服務、交通接送、輔具補助）與季節性健康提醒。

每個檔案開頭兩行為 metadata，其後空一行接正文：

```
標題: 長期照護服務對象口腔照護(病患照護版)
來源: https://dep.mohw.gov.tw/DOOH/cp-6544-71349-124.html
```

正文段落之間固定空一行——切塊時靠這個訊號斷句，比各篇不一致的標題格式可靠。

這兩行**不會**被上傳到 S3。`scripts/sync_kb.sh` 會先跑 `scripts/build_kb_upload.py`，把它們剝成 Bedrock 的 metadata sidecar（`<檔名>.metadata.json`），只上傳乾淨正文——否則標題與 URL 會被當成正文一起切塊索引。標題設 `includeForEmbedding: true`（FIXED_SIZE 切塊下，第二塊之後就沒有主題線索了），來源設 false（URL 對語意比對是雜訊，但要留給 agent 引用）。

TODO: metadata sidecar 的實際效果尚未在雲端驗證（需等環境開放後 `terraform apply` + `sync_kb.sh`），屆時要確認 `includeForEmbedding` 的欄位格式是否被目前的 Bedrock API 版本接受。
