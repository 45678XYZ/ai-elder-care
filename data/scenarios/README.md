# scenarios/ — Demo 情境對話腳本

Live Demo 排練用的端到端對話腳本。每個情境以 tool 覆蓋為設計目標，確保所有 Agent tools 至少被驗證一次。

## 檔案結構

| 檔案 | 長者 | 情境 | 覆蓋工具數 |
|------|------|------|:---:|
| `a1_morning_routine.md` | 陳阿蘭（華語） | 晨間提醒與天氣查詢 | 6 |
| `a2_emergency.md` | 陳阿蘭（華語） | 跌倒緊急通報與升級 | 5+notify×3 |
| `b1_routine_mgmt.md` | 邱秋妹（客語） | 行程建立/修改/刪除 | 5 |
| `b2_emotional.md` | 邱秋妹（客語） | 情緒陪伴與斷線恢復 | 5 |

## 輔助文件

| 檔案 | 用途 |
|------|------|
| `_format.md` | 腳本格式規範與符號說明 |
| `coverage_matrix.md` | 17 tools 覆蓋追蹤表 |
| `caregiver/` | 照護者端場景（待規劃） |

## 對應 Persona

腳本中的長者設定來自 `data/personas/`：
- `eld_001.json` — 陳阿蘭
- `eld_002.json` — 邱秋妹
