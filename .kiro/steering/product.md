---
inclusion: always
---

# 客照e點通 — 產品定位

高齡長者與家屬的智慧長照系統。長輩用語音（華語或客語）跟 AI 聊天，系統自動把對話變成結構化生活紀錄與每日摘要，照護者在同一支 App 看得到。

易讀版總覽見 [docs/framework-overview.md](../../docs/framework-overview.md)；完整規格見 [docs/framework.md](../../docs/framework.md)。

## 三大模組

| 模組 | 名稱 | 定位 |
|---|---|---|
| A | 語音互動陪伴 | 免手持語音迴圈、AgentCore Runtime 對話大腦（託管長期記憶 + 溫暖語氣）、行程提醒與完成追蹤、衛教知識庫問答 |
| B | 生活記錄與智慧摘要 | 從自然對話萃取結構化生活事件、每日產出固定七類的身心靈摘要 |
| C | 照護者資訊介面 | 即時安全警報、行程管理、每日摘要、事件時間軸與統計 |

## 單一 App 雙模式

一支 Flutter App。使用者登入後依角色進入**長者模式**（全語音、極簡大字、主要內文 ≥ 24sp）或**照護者模式**（摘要、時間軸、行程、統計）。不是兩支 App，也不是同一畫面塞兩種角色。

## 語言策略

**中文先行、客語第二階段。** 客語支援六腔：`htia_sixian`（預設）、`htia_hailu`、`htia_dapu`、`htia_raoping`、`htia_zhaoan`、`htia_nansixian`。

長者端有三顆獨立開關，**不要連動它們**：說話語言（華語／客語）、客語腔調（選了客語才有意義）、畫面文字（一般漢字／客語漢字）。講客語不等於讀得懂客語漢字。

`hakka_dialect` 是 ASR/TTS 的唯一來源，只讀 elder profile。腔調設錯會讓 ASR 聽不懂長輩說話，是會自己鎖死的失效模式——寫入失敗必須讓使用者看到。

## 產品鐵則

違反這三條會直接傷到使用者或洩漏個資：

1. **衛教僅供參考，不做醫療診斷。** 知識庫只回公開衛教內容，不下診斷、不給處方、不替代就醫判斷。
2. **PII 最小化。** 對外只回 `cg_` 開頭、由 Cognito `sub` 穩定衍生的照護者識別，不暴露 `sub`。事件只存摘要描述，不複製逐字稿；需要原文時回 `conversations` 查。政策見 [docs/pii.md](../../docs/pii.md)。
3. **權限邊界不鬆綁。** 長者只能存取自己，照護者只能存取 `caregiver_ids` 含其 `sub` 的長者。長者帳號對 `PATCH /elders` 回 403——要讓長輩改自己的偏好，走對話工具，不是放寬 REST 端點。

## 使用者旅程

情境劇本見 [docs/user-journey.md](../../docs/user-journey.md)，交付版見 [docs/deliverables/user-journey.md](../../docs/deliverables/user-journey.md)。

## 已知缺口

談功能現況前先確認這兩件還沒好（詳見 [app/README.md](../../app/README.md) 的「目前狀態」）：

- **首次設定尚未呼叫 `POST /elders`**：`setup_screen.dart` 目前只寫本機。
- **因此實務上沒有長者帳號被綁定**：後端已經支援——`POST /elders` 帶 `self_register=true` 會呼叫 `db.bind_elder_account()` 寫入 `elder_accounts`，pre-token-generation trigger 再據此注入 `elder_id` claim。但 App 沒呼叫這個端點，所以註冊時選「長輩」的帳號拿不到 claim，後端一律當照護者。畫面仍會進長者模式、資料也存取得到（建立者被綁進 `caregiver_ids`，等於自己是自己的照護者），但這不是設計上的正解。

修法是讓首次設定改呼叫 `POST /elders`（長者自建時帶 `self_register=true`），完成後 `DemoRepository` / `DemoData` / `DemoAuthBackend` 才能整批移除。
