# ai-elder-care — Flutter App

單一 App，登入後依 Cognito 角色切換：**長者模式**（`lib/elder/`，語音為主）／**照護者模式**（`lib/caregiver/`，資料管理）。共用服務在 `lib/shared/`。

架構原則：**Flutter 端做薄，智慧邏輯放後端**。API 契約以 [../docs/api.md](../docs/api.md) 為準，欄位不自創。

## 技術棧

- Flutter 3.x / Dart 3.x
- 狀態管理：目前無框架（`StatefulWidget`）；引入前先確認全隊一致
- 語音：裝置端 ASR `speech_to_text`、TTS `flutter_tts`；正式 `/chat` 回傳音檔用 `just_audio`，客語錄音走 `record`
- 網路：`http`（`lib/shared/services/api_client.dart`）

## 設計方向：暖紙手帳

紙感底 + 朱紅點綴 + Noto Serif TC 襯線 + 農民曆牌面 + 極簡。
所有 token 的唯一真實來源在 [design-system/MASTER.md](design-system/MASTER.md)，畫面截圖在 [design-system/screenshots/](design-system/screenshots/)，逐頁例外規則在 [design-system/pages/](design-system/pages/)。**改任何 theme 值前先回 MASTER.md 對。**

## UI 硬性約束（不可違反）

### 長者模式
- 主要內文最小 24sp；主要動作提示 32sp 以上（大數字如農民曆日期、麥克風狀態走 46/66）
- Touch target 最小 60dp（非 Material 預設 48dp）
- 單一畫面最多 3 個可互動元素
- 主要操作必須有語音替代路徑
- 不使用 swipe、long-press 等隱藏手勢

### 照護者模式
- 可用標準 Material density；小標籤／輔助文字可用 MASTER.md 的 11–15 級距
- Touch target 最小 48dp

### 全域
- **目標裝置是 Android 手機**，不為平板／桌面寬度另做版面。畫面本身是彈性的（Flex/Expanded），但寬螢幕下只會被拉開而非重新排版，這是接受的取捨。`web` build 僅供截圖與快速預覽用，不是出貨目標
- **介面目前單一語言（華語），尚未做 i18n**。`lang_preference`（`zh-TW`｜`hak`）目前只決定**語音**走哪條路（客語裝置端無法辨識，改錄音送後端），與畫面文字無關
- **語音語言只有長者端能切**（今日頁最底下的 `ElderLangToggle`，標籤「中文／客語」）。照護者「管理」頁那顆已移除——兩邊都能改的話，長者那份寫本機、照護者那份寫後端，同時存在就會互相覆蓋，而長輩那份必須贏（實際在說話的是他）。唯一例外是首次設定（`setup_screen`），那時長輩還沒有裝置。代價是長者那份只在本機（`PATCH /elders/{id}` 是照護者專屬端點），換裝置會退回首次設定的值
- **畫面文字可切一般漢字／客語漢字**（今日頁的 `ElderTextLangToggle`）。對照表在 `lib/shared/i18n/strings.dart`，畫面文字一律經過 `t()`／`t1()`／`t2()`，**key 是華語原文**（改文案要同步改 key）。缺譯自動退回華語，不會變空白。**對話內容不經過 `t()`**——後端回什麼就顯示什麼
- **語音與文字是兩顆獨立的鈕，不可合併**。講客語的長輩不一定讀得懂客語漢字——有人講客語但只認得一般漢字，綁在同一個開關等於逼他在「聽不懂語音」和「看不懂畫面」之間二選一。語音那顆寫「客語」（怎麼說），文字那顆寫「客語漢字」（怎麼寫）
- 所有畫面須在 `textScaler: TextScaler.linear(2.0)` 下不 overflow（用 Flex/Expanded，勿寫死 px）
- 狀態不可只靠顏色傳遞——必須搭配 icon 或文字
- 對比度目標 WCAG AAA（7:1），非 AA
- 顏色一律走 `AppColors`，禁止 hardcode hex
- 間距一律走 `AppSpacing`，禁止 magic number
- 兩模式共用 widget + 不同 theme，勿為各模式重寫

## 程式慣例（承 docs/conventions.md）

- 檔名 `snake_case.dart`；變數／函式 `lowerCamelCase`；類別／Widget `PascalCase`；常數 `lowerCamelCase`（Effective Dart）
- **註解一律繁體中文**，說明「為什麼」而非覆述程式碼；契約細節指向 `api.md`，勿另抄一份
- 未完成處用 `// TODO:`／`// FIXME:`
- 提交前跑 `dart format` 與 `flutter analyze`（無 error）
- 遵循 Effective Dart 與 `flutter_lints`

## 目錄

```
app/
├── CLAUDE.md              ← 本檔
├── design-system/         ← 設計來源（token 表、截圖、逐頁規則）
├── lib/
│   ├── theme/             ← MASTER.md → AppColors/AppTypography/AppSpacing/AppTheme（Phase 3）
│   ├── elder/screens/     ← 長者模式
│   ├── caregiver/screens/ ← 照護者模式
│   └── shared/            ← config / models / services
└── test/
```
