# 開發慣例

命名、註解與程式風格的團隊約定。API 欄位與 ID 命名以 [api.md](api.md) 為準、資料表命名見 [framework.md](framework.md)。

## 命名

**檔案名**

- Python：`snake_case.py`（如 `summary_generator.py`）
- Dart：`snake_case.dart`
- Terraform：依資源領域分檔、檔名 `snake_case.tf`（如 `api_gateway.tf`、`dynamodb.tf`）

**Python（PEP 8）**

- 函式、變數、模組：`snake_case`
- 類別：`PascalCase`
- 常數、環境變數鍵：`UPPER_SNAKE_CASE`

**Dart**

- 變數、函式、參數：`lowerCamelCase`
- 類別、Widget、type：`PascalCase`
- 常數：`lowerCamelCase`（依 Effective Dart，不用 `UPPER_SNAKE`）

**Terraform**

- resource／variable／output 名稱：`snake_case`
- resource 的 local name 以用途命名，不用型別或 `this`／`main` 這類泛稱

**API 與資料**

- request／response 欄位一律 `snake_case`；實體 ID 帶型別前綴（`eld_`／`rtn_`／`evt_`／`cnv_`）。完整規則見 [api.md](api.md)——實作時照著用，不自創欄位名
- DynamoDB 資料表名用小寫（`elders`、`conversations`…），見 [framework.md](framework.md)

## 註解與 docstring

- **一律用繁體中文**，與現有程式一致
- 每個 handler／模組頂部寫 docstring 說明用途；API handler 一併指向規格（`規格見 docs/api.md`），資料流複雜者以條列描述——比照 `backend/src/handlers/chat.py`
- **註解說明「為什麼」，不覆述程式碼在做什麼**；契約與規格細節指向 `api.md`，別在註解裡另抄一份，以免兩邊走鐘
- 未完成處用 `# TODO:`／`# FIXME:` 標記

## 程式風格與提交前檢查

- **Python**：遵循 PEP 8；提交後端變更前跑 `python -m pytest`
- **Dart／Flutter**：遵循 Effective Dart 與 `flutter_lints`；提交前跑 `dart format` 與 `flutter analyze`
- **Terraform**：提交前跑 `terraform fmt`

## 後端回應

- 所有 API 回應走 `src.shared.responses` 的統一格式，不各自手刻；錯誤結構與狀態碼對應見 [api.md](api.md)
- handler 以 `from src.shared import ...` 匯入共用模組（`auth`／`db`／`responses`），見 [../backend/README.md](../backend/README.md)
