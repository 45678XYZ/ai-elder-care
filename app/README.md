# app/ — Flutter App

單一 App，登入後依 Cognito 角色切換長者模式（`lib/elder/`）／照護者模式（`lib/caregiver/`）；共用服務在 `lib/shared/services/`。

## 初始化

本骨架只含 `pubspec.yaml` 與 `lib/`。第一次開發前先產生 Android 平台檔案：

```bash
cd app
flutter create --platforms android --project-name e_hakka_care .
flutter pub get
```

## 結構

```
lib/
├── main.dart                 # 進入點：登入 → 依角色切換模式
├── elder/screens/            # 長者模式：語音對話、今日行程
├── caregiver/screens/        # 照護者模式：長者管理、每日摘要、統計圖表、事件時間軸
└── shared/
    ├── config/               # api_config（後端位址，可用 --dart-define=API_BASE_URL 覆寫）
    ├── models/               # API 回應型別（ask_result / chat_reply / elder / daily_summary / life_event / routine / stats）
    └── services/             # api_client / auth / speech（ASR）/ audio（TTS）/ notification
```

## 目前進度

長者語音問答第一版：裝置端華語 ASR → RAG PoC `/ask` → 裝置端 TTS 唸回覆 →
自動再聆聽（免手持迴圈），另有打字備援。正式 `/chat`、Cognito 登入、客語（改走
audio 送後端辨識）、照護者模式各畫面尚未實作。

本機實測需先啟動後端 RAG（見 [backend/README.md](../backend/README.md)）；模擬器預設
連 `10.0.2.2:8000`，桌面/web 用 `flutter run --dart-define=API_BASE_URL=http://localhost:8000`。

API 規格見 [docs/api.md](../docs/api.md)。
