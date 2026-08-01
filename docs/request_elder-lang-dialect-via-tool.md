# 需求：`update_elder_profile` 增加 `lang_preference` 與 `hakka_dialect`

提出者：App 端。影響範圍：`docs/llm_tools.md`、`backend/src/handlers/tools.py`、`docs/api.md`（工具契約那一段）。

---

## 一句話

長輩沒有任何辦法改自己的說話語言與客語腔調。請在 `update_elder_profile` 加這兩個參數，讓他用講的（或打字的）就能改。

## 為什麼現在改不了

`hakka_dialect` 與 `lang_preference` 都存在 elder profile，而寫入 profile 只有兩條路：

| 路徑 | 誰能用 | 現況 |
|---|---|---|
| `POST /elders` | 照護者 | 只在建立時寫一次 |
| `PATCH /elders/{id}` | 照護者 | `elders.py:146` 對長者帳號回 403 |
| `update_elder_profile`（工具） | 長者（對話中） | **參數只有 `health_note_to_add`／`habit_note_to_append`／`nickname`** |

App 端已經把語言鈕放在長者端（今日頁最下方），但那顆鈕只寫得到本機。對**語言**來說還過得去——`POST /chat` 每次都帶 `lang`，後端用的是 `req.lang`（`chat.py:474,536`），所以當下生效。對**腔調**則完全無效：後端只讀 profile 的 `hakka_dialect`（`chat.py:537`，api.md 也明寫「App 不在 `/chat` 傳腔調」）。

結果是：**腔調一旦在初次設定填錯，長輩自己永遠改不回來**，而照護者未必知道長輩講哪一腔。

## 為什麼不直接鬆綁 PATCH

因為 `PATCH /elders` 是整份欄位白名單，同一個請求也能寫 `health_notes` 與 `family`。那些是照護紀錄，讓長輩自己改（或誤刪家屬名單）的風險跟改語言設定完全不同性質。

`update_elder_profile` 由後端自己決定寫哪些欄位，加參數不會順帶把 `health_notes` 的寫入權給出去，也不用動 REST 的授權模型。所以走工具這條比較安全。

## 要加什麼

```
update_elder_profile(
    elder_id,
    health_note_to_add,      # 既有
    habit_note_to_append,    # 既有
    nickname,                # 既有
    lang_preference,         # 新增：zh-TW | hak
    hakka_dialect,           # 新增：htia_sixian | htia_hailu | htia_dapu
                             #       | htia_raoping | htia_zhaoan | htia_nansixian
)
```

值域與 `docs/api.md` 的 enum 一致，非法值比照現有欄位處理。

**兩個要一起加。** 只加腔調的話，長輩說「我改講客語」語言不會變；只加語言的話，海陸腔的長輩換成客語之後仍被當四縣腔辨識，等於換完照樣聽不懂。

### 觸發語句範例

| 長輩說 | 期望寫入 |
|---|---|
| 「我講海陸腔」「𠊎講海陸」 | `hakka_dialect=htia_hailu` |
| 「跟我講客話」 | `lang_preference=hak` |
| 「還是講國語好了」 | `lang_preference=zh-TW` |
| 「我是四縣的」 | `hakka_dialect=htia_sixian` |

腔調名稱長輩多半說簡稱（四縣／海陸／大埔／饒平／詔安／南四縣），tool description 裡建議把六個中文名都列出來，讓模型對得上。

## 這兩個欄位的誤寫風險跟其他欄位不同，請加保護

呼叫這個工具的是模型，不是長輩——長輩只是講話，是對話大腦自己判斷要不要寫、寫什麼。
現有三個欄位這樣做風險可控，但**腔調不是**：

| 欄位 | 寫錯的後果 | 怎麼修 |
|---|---|---|
| `health_note_to_add` | 照護者看到一筆不對的註記 | 管理頁刪掉；而且帶 `source: "agent"`，看得出是 AI 聽來的 |
| `nickname` | 稱呼怪 | 照護者改掉 |
| **`hakka_dialect`** | **ASR 立刻聽不懂長輩說話** | **長輩要修正就得再講一次話——但他已經講不通了** |

這是會自己把自己鎖死的失效模式，而誤觸發並不難想像：長輩說「**我隔壁鄰居**講海陸腔」，
模型很可能就把他自己的腔調改成海陸。

建議三道保護，重要性由高到低：

1. **只在長輩明確講自己時才寫。** 第三人稱提及（鄰居、朋友、以前住的地方）一律不寫。
   這一條寫進 tool description 就有效，成本最低
2. **寫入前先確認一次。** 「你係講海陸腔係無？」得到肯定再寫。多一輪對話換掉一個
   自己鎖死的風險，划算
3. **變更要讓照護者看得到。** 比照 `health_notes` 的 `source` 標示——照護者是唯一
   還能救的人（他的 `PATCH /elders` 沒有被擋），但前提是他知道被改過

第 1 點請務必做，第 2、3 點看你們的成本評估。

## 一個邊界情況要注意

**腔調錯的時候，ASR 聽不懂長輩說話，他就講不出「我講海陸腔」這句。**

目前的解法是聊天頁的打字備援——長輩可以打字，`/chat` 收 `text` 一樣會進對話大腦。請確認**打字那條路也會觸發 tool calling**，否則這個功能對最需要它的人（腔調設錯的那些）仍然無效。

## App 端會怎麼配合

- 這兩個欄位加上去之後，App **不需要任何改動**就能受益：長輩用講的改，後端寫 profile，下一輪對話就生效
- App 端今日頁那顆語言鈕維持現狀（管當下這台裝置、立刻生效）。等 profile 也跟著更新之後，換裝置就不會再退回舊值
- `Elder` model 已經有 `hakka_dialect` 欄位，`GET /elders` 回什麼都讀得到

## 順帶回報一個現況

App 移除了照護者管理頁的語言切換（理由：兩邊都能改會互相覆蓋，而實際在說話的是長輩）。副作用是 `lang_preference` 目前**只有 `POST /elders` 寫得進去，之後誰都改不了**。這個需求做完就一併解決了。
