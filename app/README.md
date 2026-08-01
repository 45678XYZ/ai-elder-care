# app/ — Flutter App

單一 App，登入後依角色切換**長者模式**（`lib/elder/`，語音為主）／**照護者模式**（`lib/caregiver/`，資料管理），共用服務在 `lib/shared/`。

- API 契約：[docs/api.md](../docs/api.md)（欄位不自創）
- 設計 token 唯一真實來源：[design-system/MASTER.md](design-system/MASTER.md)
- 開發約束與慣例：[CLAUDE.md](CLAUDE.md)

## 跑起來

平台檔案（`android/`、`web/`）已在版控內，不需要再跑 `flutter create`。

```bash
cd app
flutter pub get
flutter run                # Android 裝置／模擬器
```

預設跑 **demo 資料**，不需要後端也能點完所有畫面。

### 看畫面（不接後端）

```bash
flutter build web
cd build/web && python -m http.server 8080
```

開 <http://127.0.0.1:8080/phone.html> —— 手機比例的預覽外框，可直接切換長者／照護者身分，不必真的登入。`web` build 只服務預覽與截圖，**不是出貨目標**（目標裝置是 Android 手機）。

### 接真後端

資料來源收斂在 `CareRepository` 一個介面後面，切換是一個 `--dart-define`：

```bash
flutter run \
  --dart-define=API_BASE_URL=https://xxx.execute-api.ap-northeast-1.amazonaws.com \
  --dart-define=USE_BACKEND=true
```

不帶 `USE_BACKEND` 就是 demo（`DemoRepository`），帶了走 `ApiRepository`。畫面兩邊共用，不需要改任何一行。

### 檢查

```bash
dart format lib test
flutter analyze          # 必須 0 error
flutter test
```

## 結構

```
app/
├── CLAUDE.md              # 開發約束（UI 硬性規格、命名、註解慣例）
├── design-system/         # 設計來源：MASTER.md token 表、screenshots/、pages/ 逐頁例外
├── i18n/                  # 客語漢字待翻／待確認的句子（純文字，翻完收進 lib/shared/i18n/）
├── assets/                # images（早安圖等素材）、fonts（Noto Serif TC）
├── android/ web/          # 平台檔案；web/phone.html 是預覽外框
└── lib/
    ├── main.dart          # 進入點
    ├── app_router.dart    # go_router：路由與未登入的導向守衛
    ├── theme/             # MASTER.md → AppColors / AppTypography / AppSpacing / AppTheme
    ├── elder/             # 長者模式
    │   ├── elder_shell.dart   # 底部 2 tab（聊天／今日行程）
    │   ├── screens/           # chat / today / calendar_enlarged / link_caregiver
    │   └── widgets/           # 農民曆牌面、撕曆動畫、問候語、語言切換鈕
    ├── caregiver/         # 照護者模式
    │   ├── caregiver_shell.dart
    │   └── screens/           # elders（管理）/ summaries / stats / timeline / setup
    └── shared/
        ├── config/        # api_config：baseUrl 與 USE_BACKEND 旗標
        ├── i18n/          # strings.dart：介面文字的客語漢字對照與 t()
        ├── models/        # api.md 回應型別
        ├── screens/       # 登入、註冊、驗證、角色選擇、隱私政策
        ├── services/      # 見下
        └── widgets/       # 兩模式共用元件（卡片、四態外殼、狀態標籤、表單）
```

`shared/services/` 的幾條主線：

| 檔案 | 做什麼 |
|---|---|
| `care_repository.dart` | 資料來源介面 + demo／真後端的切換點 |
| `api_client.dart` / `api_repository.dart` | 照 api.md 打端點的薄層 |
| `demo_repository.dart` / `demo_data.dart` | demo 假資料，行為對齊真後端 |
| `auth_service.dart` / `demo_auth_backend.dart` | 登入狀態；Cognito 尚未接上（見檔尾 TODO） |
| `session_store.dart` | 本機情境：目前長者、語音語言、畫面文字語言，全部綁帳號 `sub` |
| `chat_session.dart` | 對話 session 與冪等鍵代管 |
| `speech_service.dart` / `audio_service.dart` / `audio_recorder_service.dart` | 裝置端 ASR、TTS、客語錄音 |
| `routine_sync.dart` / `notification_service.dart` | 行程變動廣播與本地提醒重排 |
| `lunar_date.dart` / `taiwan_holiday.dart` | 農民曆與台灣假日（決定牌面紅／藍） |

## 目前狀態

**已完成**

- 長者模式：語音問答免手持迴圈（裝置端華語 ASR → TTS，打字備援）、今日行程與手動完成、農民曆撕曆頁首、連結家人
- 照護者模式：長輩管理（健康狀況／生活習慣／家人／例行公事增刪改）、每日摘要、統計、事件時間軸
- 長者端可自行切換**說話語言**（華語／客語）與**畫面文字**（一般漢字／客語漢字），兩者獨立
- demo 資料完整，不接後端可跑完整個流程

**未完成**

- **Cognito 登入**：目前是本機 demo 帳號（`DemoAuthBackend`），換裝置就沒有身分記錄
- **正式 `/chat`**：目前接的是 RAG PoC 的 `/ask`，沒有語音回覆與 session
- **客語語音**：`lang_preference` 已能切，但 `chat_screen` 仍走華語辨識迴圈，錄音送後端那條未接
- `POST /elders`：首次設定只寫本機，尚未建到後端

後端上線的切換順序：**Cognito 先於一切**，沒有 token 其餘端點都會 401。
