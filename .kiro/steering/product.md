# Product

智慧長照陪伴 App：長者模式（免手持語音互動陪伴）＋照護者模式（生活記錄摘要與資訊介面）。

- 系統框架：docs/framework.md
- API 規格：docs/api.md（前後端的唯一契約，改動須同步）
- 架構原則：Flutter 端做薄、智慧邏輯放 AWS 後端（Python Lambda）；IaC 用 Terraform
- 本機可用 OpenTofu CLI 驗證 IaC，但 `.tf`、文件與 `.terraform.lock.hcl` 交付格式維持 Terraform；不得留下 OpenTofu registry/hash 改寫。
