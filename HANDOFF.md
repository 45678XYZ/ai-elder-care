# 交接紀錄（2026-07-28）

> 給接手的人／新 session：讀完這份就能接著做。這是**當下狀態的快照**，
> 過期就整份覆蓋（上一份 07-19 的版本已被這份取代），不要在裡面累積歷史。

## 一句話狀態

`app/` 的**畫面與設計系統已經做完並可以 demo**（12 條路由全部走得通），但**所有資料都是
本機假資料**（`DemoData`）、**登入是假後端**（驗證碼固定 `123456`）——後端 API 一上線就是
照 `docs/api.md` 把 TODO 逐一換掉。

## 專案與分工

- **ai-elder-care**（2026 雲湧智生黑客松）智慧長照陪伴 App。Repo：`45678XYZ/ai-elder-care`
- Boyu 只負責 **`app/`（Flutter）**；`backend/`、`terraform/` 是隊友的，不主動動
- 架構：Flutter 薄前端 + AWS（API GW / Lambda / DynamoDB / Cognito / Bedrock / Polly）
- 規格文件：`docs/framework.md`、`docs/api.md`（**契約以 api.md 為準，欄位不自創**）
- App 內規：`app/CLAUDE.md`（長者模式硬性約束都在裡面）

## 分支現況

- 目前在 `feature/app-screens-and-reminders`，**領先 origin 5 個 commit（尚未 push）**
- 相對 `main` 共 8 個 commit，都是 `app/` 的畫面與設計系統
- 規範：從最新 `main` 開分支、Conventional Commits、**資料檔絕不上傳**

## 畫面完成度（`app/lib/app_router.dart`）

| 路由 | 畫面 | 狀態 |
|---|---|---|
| `/auth/sign-in` `/auth/sign-up` `/auth/verify` | 登入／註冊／信箱驗證 | 版面完成，走 `DemoAuthBackend`（驗證碼 `123456`），**Cognito 未接** |
| `/setup` | 初次設定（長者姓名、暱稱、語言） | 完成；資料存 `shared_preferences`，**未 POST /elders** |
| `/` | 角色選擇 | 完成（正式版應由 token 的 `elder_id` claim 分流） |
| `/elder/today` | 長者今日：農民曆牌面 + 行程 | 完成，資料來自 `DemoData` |
| `/elder/chat` | 長者語音對話 | 版面完成，裝置端 ASR/TTS 可跑；**客語走錄音送後端那條路徑未做** |
| `/elder/link` | 連結家人 | 完成（本機狀態） |
| `/care/summary` `/care/timeline` `/care/stats` `/care/manage` | 照護者四頁 | 完成，資料來自 `DemoData` |

## 後端串接：全部集中在 TODO

`lib/shared/services/api_client.dart` 已按 `api.md` 寫好，但**畫面都還沒用它**。要接的點：

```
lib/main.dart:37                      getRoutines
lib/elder/screens/today_screen.dart   :46 getDailyRoutines  :62 completeRoutine
lib/caregiver/screens/elders_screen.dart      :48 :70 :102 :138
lib/caregiver/screens/summaries_screen.dart   :43 getSummaries  :53 generateSummary
lib/caregiver/screens/timeline_screen.dart    :52 getEvents
lib/caregiver/screens/stats_screen.dart       :42 getStats
lib/caregiver/screens/setup_screen.dart       :80 POST /elders
lib/shared/services/session_store.dart        :110 getAllElders  :129 POST /elders
lib/shared/services/demo_data.dart            整檔在後端上線後刪除
```

## 設計系統

- **唯一真實來源：`app/design-system/MASTER.md`**。改 theme 值前先回去對
- 暖紙手帳：紙感底 + 朱紅點綴 + Noto Serif TC 襯線 + 農民曆牌面
- **農民曆牌面** `lib/elder/widgets/almanac_face.dart` 是三處共用的同一個 widget
  （首頁小卡、撕曆過場、放大檢視），版面完全相同、只差大小。字級是一組比例，
  整組乘上「牌面寬度 / 326」；大日期撐滿中段並置中。細節見 MASTER.md 那一節
- 截圖在 `app/design-system/screenshots/built/`，產生方式寫在同目錄 README

## 測試與檢查

- `flutter analyze` 無 issue、`flutter test` **137 個全過**
- 硬約束由測試守住：`textScaler 2.0` 不 overflow、320／360／412 三種寬度不 overflow、
  螢幕報讀標籤、農曆換算正確性（`lunar_accuracy_test.dart`）
- 測試檔：`test/*.dart`（`_tmp_*.dart` 是暫存探針，**要刪**，見下）

## 本機工具

- 預覽：`flutter build web --release` → 用任一靜態 server 提供 `build/web`
  （`web/phone.html` 是手機尺寸預覽外框，可切畫面與尺寸，build 時會自動帶進去）
- 截圖：不需要 Playwright，`chrome --headless=new --window-size=390,844
  --remote-debugging-port=9222` + CDP 就能截。踩過的雷都寫在
  `app/design-system/screenshots/README.md`

## 待處理（照優先序）

1. **`app/pubspec.yaml` 與 `app/assets/` 要一起 commit**：pubspec 已宣告 `assets/images/`，
   但圖檔還沒進版控——只提交 pubspec 的話別人 clone 下來 `flutter build` 會直接失敗。
   圖的來源與授權要先確認
2. **缺 `assets/images/greeting_evening.jpg`**：晚上（18 點後）的早安圖找不到檔案，
   畫面會退回色塊。`assets/images/90848.jpg` 目前沒被任何程式碼用到
3. **刪掉 `app/test/_tmp_*.dart` 四支暫存探針**，其中 `_tmp_face_shot.dart` 會讓
   `flutter test` 失敗
4. `lib/elder/screens/chat_screen.dart` 有未 commit 的改動（聊天頁改版進行中）
5. `.gitignore` 建議補上常駐的未追蹤檔：`.gradle-home/`、`backend/*.log`、
   `HANDOFF.md`、`app/design-system/*.bak.md`
6. 後端上線後照上面的 TODO 清單接 API
7. 客語路線：裝置端 ASR 不支援客語，要改走 `record` 錄音送 `/chat`

## 已知取捨與坑

- **字體是 `google_fonts` 執行期下載**：第一次啟動（或離線）會先看到系統預設字。
  撕曆過場已經先 `await GoogleFonts.pendingFonts()` 再播，避免播到一半換字。
  要根治得把 Noto Serif／Sans TC 打包進 assets（APK 會大好幾 MB）
- **對話框路由上沒有 `Material` 祖先，Flutter 會用內建錯誤樣式畫文字（黃色底線）**。
  自己開 `showGeneralDialog` 時記得包一層 `Material(type: transparency)`
- 撕曆過場：**進到今日畫面一定播**；「一天只播一次」只用在 App 切回前景那條路徑。
  系統開「減少動態效果」則完全不播
- 國定假日（雙十、清明補假）判不出來，目前只認週末與農曆節日
- Android build：C 槽空間小，Gradle 家目錄已改到 `D:\Hackthon\.gradle-home`、
  daemon 關閉、heap 2G
- 介面**單一語言（華語）**，不做 i18n；`lang_preference` 只決定語音走哪條路，
  且只能在照護者「管理」頁改
