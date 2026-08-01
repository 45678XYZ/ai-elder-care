# app/ — Flutter 跨平台應用程式

本目錄為專案的行動端應用程式，採用 **Flutter** 框架開發。單一 App、雙模式切換架構：使用者登入後依 Cognito 角色自動進入「長者陪伴模式」或「照護者管理模式」。

設計方向為「暖紙手帳」：紙感底色 + 朱紅點綴 + Noto Serif TC 襯線 + 農民曆牌面 + 極簡大字體。

## 模組功能

1. **長者模式 (Elder Mode)** — 全語音互動陪伴、極簡大字介面（主要內文 ≥ 24sp）、行程與用藥語音提醒
2. **照護者模式 (Caregiver Mode)** — AI 每日摘要、生活事件時間軸、行程管理、互動統計
3. **共用基礎設施** — API Client、Cognito 身份驗證、語音服務、本地通知

## 目錄結構

```
app/
├── lib/                  # Flutter 原始碼
│   ├── main.dart         # 程式進入點
│   ├── app_router.dart   # GoRouter 路由配置
│   ├── theme/            # 設計系統實作
│   ├── elder/            # 長者模式
│   ├── caregiver/        # 照護者模式
│   └── shared/           # 共用模組
├── assets/               # 靜態資源
├── design-system/        # 設計規範文件
├── i18n/                 # 客語漢字待翻／待確認的句子（翻完收進 lib/shared/i18n/）
├── test/                 # 測試
├── android/              # Android 平台設定
├── web/                  # Web 平台設定
├── pubspec.yaml          # 專案設定
└── README.md
```

---

## lib/ 詳細檔案說明

### 進入點與路由

| 檔案 | 功能 |
|------|------|
| `main.dart` | 程式進入點：初始化 AuthService → 還原登入狀態 → 載入 AppSession → 初始化通知 → 啟動 App → 排程 routine 同步 |
| `app_router.dart` | GoRouter 路由配置：根據登入狀態與角色（長者/照護者）決定導航路徑，含 redirect 邏輯 |

### theme/ — 設計系統實作

| 檔案 | 功能 |
|------|------|
| `app_theme.dart` | `AppColors`（全域色彩 token）、`AppTypography`（字型階層）、`AppSpacing`（間距系統）、`AppTheme`（MaterialApp theme）。所有值源自 `design-system/MASTER.md` |

### elder/ — 長者模式

| 檔案 | 功能 |
|------|------|
| `elder_shell.dart` | 長者模式外殼（底部導航、畫面切換框架） |
| `screens/today_screen.dart` | 今日總覽畫面：農民曆日期牌面、時段問候語、待辦行程提示、語音入口 |
| `screens/chat_screen.dart` | 語音對話主畫面：麥克風按鈕、語音辨識 → 送 `POST /chat` → 播放 AI 語音回覆 |
| `screens/calendar_enlarged.dart` | 放大版農民曆：大字體日期顯示，方便長者確認今天日期 |
| `screens/link_caregiver_screen.dart` | 綁定照護者畫面：輸入照護者代碼完成家屬綁定 |
| `widgets/almanac_face.dart` | 農民曆牌面 Widget：顯示國曆/農曆日期與節氣 |
| `widgets/calendar_tear.dart` | 撕頁日曆 Widget：模擬日曆撕頁效果的日期顯示 |
| `widgets/greeting_slot.dart` | 時段問候語 Widget：依早/午/晚顯示不同問候語與背景圖 |
| `widgets/lang_toggle.dart` | 長者自行切換語言的兩顆鈕：`ElderLangToggle`（說話：華語／客語）與 `ElderTextLangToggle`（畫面文字：一般漢字／客語漢字）。兩者獨立，講客語不等於讀得懂客語漢字 |

### caregiver/ — 照護者模式

| 檔案 | 功能 |
|------|------|
| `caregiver_shell.dart` | 照護者模式外殼（底部導航、畫面切換框架） |
| `screens/elders_screen.dart` | 長者列表畫面：顯示照護者綁定的所有長者，可切換查看對象 |
| `screens/summaries_screen.dart` | 每日摘要畫面：顯示 AI 每日為長者生成的健康與生活摘要 |
| `screens/timeline_screen.dart` | 事件時間軸畫面：以時間倒序呈現長者近期生活事件（用藥、活動、安全事件等） |
| `screens/stats_screen.dart` | 統計圖表畫面：互動輪數趨勢、行程完成率、逐日數據 |
| `screens/setup_screen.dart` | 設定管理畫面：長者資料編輯、行程管理、通知偏好 |

### shared/ — 共用模組

#### shared/config/

| 檔案 | 功能 |
|------|------|
| `api_config.dart` | API 端點設定：baseUrl（可透過 `--dart-define=API_BASE_URL` 覆寫）、各 endpoint 路徑 |

#### shared/models/

| 檔案 | 功能 |
|------|------|
| `elder.dart` | 長者資料模型（對應後端 ElderResponse） |
| `caregiver.dart` | 照護者資料模型 |
| `routine.dart` | 例行公事模型（定義 + occurrence 狀態） |
| `life_event.dart` | 生活事件模型（對應後端 EventResponse） |
| `daily_summary.dart` | 每日摘要模型 |
| `chat_reply.dart` | 對話回覆模型（文字 + 音訊 URL） |
| `ask_result.dart` | 問答結果模型 |
| `stats.dart` | 統計資料模型 |
| `session_close.dart` | Session 關閉結果模型 |
| `api_page.dart` | 分頁結果通用模型（含 next_token） |

#### shared/services/

| 檔案 | 功能 |
|------|------|
| `api_client.dart` | HTTP Client 封裝：帶 Cognito JWT token 的統一請求入口，處理錯誤與重試 |
| `api_repository.dart` | API 資料倉庫：封裝所有後端 API 呼叫（chat / elders / events / summaries / routines / stats） |
| `api_exception.dart` | API 例外定義：將後端錯誤碼映射為 Dart 例外 |
| `api_error_codes.dart` | 錯誤碼常數定義（對應 docs/api.md 的 error codes） |
| `auth_service.dart` | 認證服務：Cognito 登入/註冊/登出/還原、管理 JWT token 與使用者身分 |
| `auth_backend.dart` | 認證後端介面抽象（可切換 Cognito 與 Demo 實作） |
| `demo_auth_backend.dart` | Demo 模式認證實作（離線展示用） |
| `demo_repository.dart` | Demo 模式資料倉庫（提供假資料供離線展示） |
| `demo_data.dart` | Demo 模式假資料定義 |
| `care_repository.dart` | 照護資料倉庫：根據模式切換真實 API 或 Demo 資料來源 |
| `chat_session.dart` | 對話 Session 管理：維護與後端的 session 狀態、send/close 操作 |
| `session_store.dart` | App 本地 Session 持久化：首次設定狀態、角色選擇、已選長者、說話語言與畫面文字語言（全部綁帳號 `sub`） |
| `audio_service.dart` | 音訊播放服務：播放後端回傳的 AI 語音回覆（just_audio） |
| `audio_recorder_service.dart` | 錄音服務：客語模式錄製音訊送後端 ASR |
| `speech_service.dart` | 語音辨識服務：裝置端 ASR（speech_to_text）將語音轉文字 |
| `notification_service.dart` | 本地通知服務：排程行程提醒通知 |
| `routine_sync.dart` | Routine 同步：從後端拉取最新行程定義，重排本地提醒通知 |
| `calendar_tear_store.dart` | 日曆撕頁狀態管理：記錄今日是否已「撕」 |
| `health_note_ack_store.dart` | 健康提醒已讀狀態管理 |
| `lunar_date.dart` | 農曆日期計算：國曆轉農曆、節氣判定 |
| `taiwan_holiday.dart` | 台灣假日判定：公眾假期與補班日查詢 |

#### shared/i18n/

| 檔案 | 功能 |
|------|------|
| `strings.dart` | 長者端介面文字的客語漢字對照表與 `t()`／`t1()`／`t2()`。key 是華語原文，缺譯原樣回華語不會變空白。**對話內容不經過它**——後端回什麼就顯示什麼 |

#### shared/screens/

| 檔案 | 功能 |
|------|------|
| `sign_in_screen.dart` | 登入畫面 |
| `sign_up_screen.dart` | 註冊畫面 |
| `verify_email_screen.dart` | Email 驗證畫面 |
| `role_select_screen.dart` | 角色選擇畫面（長者 / 照護者） |
| `consent_policy_screen.dart` | 隱私政策同意畫面 |

#### shared/widgets/

| 檔案 | 功能 |
|------|------|
| `app_card.dart` | 通用卡片 Widget |
| `async_view.dart` | 非同步載入狀態 Widget（loading / error / data） |
| `care_header.dart` | 照護者模式頁面標題 Widget |
| `form_widgets.dart` | 表單通用 Widget（輸入框、下拉選單等） |
| `sign_out_button.dart` | 登出按鈕 Widget |
| `status_chip.dart` | 狀態標籤 Widget（pending / done / missed） |

---

## assets/ — 靜態資源

| 子目錄/檔案 | 說明 |
|-------------|------|
| `fonts/NotoSansTC-*.otf` | 思源黑體 TC（Black / Bold / Medium） |
| `fonts/NotoSerifTC-Black.otf` | 思源宋體 TC（農民曆等重點文字） |
| `fonts/TWSungHakka.ttf` | 客語缺字遞補（2.4 KB）：`𠊎` U+2028E、`吂` U+5402 兩字不在 Noto TC 的收字範圍，缺了會變豆腐方塊。從全字庫正宋體裁出，重建指令見 `AppTypography.hakkaFallback` |
| `images/greeting_morning.jpg` | 早安問候語背景圖 |
| `images/greeting_afternoon.png` | 午安問候語背景圖 |
| `images/greeting_evening.png` | 晚安問候語背景圖 |

---

## test/ — 測試

| 檔案 | 測試範圍 |
|------|----------|
| `auth_service_test.dart` | AuthService 登入/登出/還原邏輯 |
| `auth_screens_test.dart` | 登入/註冊/驗證畫面 Widget 測試 |
| `chat_session_test.dart` | 對話 Session 管理邏輯 |
| `calendar_tear_test.dart` | 撕頁日曆邏輯 |
| `lunar_date_test.dart` / `lunar_accuracy_test.dart` | 農曆計算正確性 |
| `taiwan_holiday_test.dart` | 台灣假日判定 |
| `routine_sync_test.dart` | Routine 同步邏輯 |
| `notification_schedule_test.dart` | 通知排程邏輯 |
| `greeting_slot_test.dart` | 時段問候語邏輯 |
| `first_run_flow_test.dart` | 首次啟動流程 |
| `link_caregiver_test.dart` | 綁定照護者流程 |
| `screens_smoke_test.dart` | 各畫面基本渲染（冒煙測試） |
| `session_scope_test.dart` | Session scope 邏輯 |
| `silence_detection_test.dart` | 靜音偵測邏輯 |
| `event_category_test.dart` | 事件分類邏輯 |
| `health_notes_test.dart` | 健康注意事項邏輯 |
| `elder_lang_toggle_test.dart` | 說話語言切換；重點是長者選華語時切得回來（不被照護者設的 `lang_preference` 蓋掉） |
| `elder_text_lang_test.dart` | 畫面文字語言切換；重點是兩顆鈕互不連動，以及切換後另一顆的標題要跟著換 |
| `routine_delete_test.dart` | 刪除例行公事走 `DELETE /routines/{id}`，不是舊的 `PATCH active:false` |

---

## 開發指南

平台檔案（`android/`、`web/`）已在版控內，**不要再跑 `flutter create`**——那會蓋掉現有設定。

```bash
cd app
flutter pub get
flutter run          # Android 裝置／模擬器
```

預設跑 **demo 資料**，不需要後端也能點完所有畫面。

### 看畫面（不接後端）

```bash
flutter build web
cd build/web && python -m http.server 8080
```

開 <http://127.0.0.1:8080/phone.html>——手機比例的預覽外框，可直接切換長者／照護者身分，不必真的登入。`web` build 只服務預覽與截圖，不是出貨目標（目標裝置是 Android 手機）。

### 接真後端

資料來源收斂在 `CareRepository` 一個介面後面，切換是一個 `--dart-define`：

```bash
flutter run \
  --dart-define=API_BASE_URL=https://xxx.execute-api.ap-northeast-1.amazonaws.com \
  --dart-define=USE_BACKEND=true
```

不帶 `USE_BACKEND` 就是 demo（`DemoRepository`），帶了走 `ApiRepository`；畫面兩邊共用，不需要改任何一行。本機 RAG PoC 則是模擬器連 `10.0.2.2:8000`、桌面／網頁用 `--dart-define=API_BASE_URL=http://localhost:8000`。

提交前請確保：
```bash
dart format .
flutter analyze    # 不得有 error
flutter test       # 所有測試通過
```

---

## 目前狀態

**已完成**

- 長者模式：語音問答免手持迴圈、今日行程與手動完成、農民曆撕曆頁首、連結家人
- 兩種語言的輸入路徑都通了：華語裝置端辨識送 `text`、客語錄音送 `audio` 由後端辨識（api.md 兩種都收）
- `POST /chat` 已接上（`CareRepo.chat()` → `ApiRepository` → `ChatSession`）；RAG PoC 的 `/ask` 只剩 demo 模式在用
- 照護者模式：長輩管理（健康狀況／生活習慣／家人／例行公事增刪改）、每日摘要、統計、事件時間軸
- 長者端可自行切換**說話語言**（華語／客語）與**畫面文字**（一般漢字／客語漢字），兩者獨立
- demo 資料完整，不接後端可跑完整個流程

**未完成**

- **Cognito 登入**：目前是本機 demo 帳號（`DemoAuthBackend`），換裝置就沒有身分記錄。見 `auth_service.dart` 檔尾的 `TODO(cognito)`
- **`POST /elders`**：首次設定只寫本機（`setup_screen.dart`），尚未建到後端
- **`elder_accounts` 表（sub → elder_id）**：目前沒有任何程式碼寫入。長者帳號登入後要靠它對應到自己是哪一位長輩——這是後端的部分

## 後端上線的切換順序

順序不能顛倒，**Cognito 先於一切**，沒有 token 其餘端點一律 401：

1. 部署 terraform，拿到 API endpoint 與 Cognito User Pool ID / Client ID
2. 接 amplify_auth_cognito，`ApiClient` 的 `tokenProvider` 指向真 token
3. 首次設定改呼叫 `POST /elders`，並寫入 `elder_accounts`
4. 最後才切 `--dart-define=USE_BACKEND=true`——它只是最後一個開關，不是中間步驟

切過去之後 `DemoRepository` / `DemoData` / `DemoAuthBackend` 就可以整批移除。
