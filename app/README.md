# app/ — Flutter App

單一 App，登入後依 Cognito 角色切換長者模式（`lib/elder/`）／照護者模式（`lib/caregiver/`）；共用服務在 `lib/shared/services/`。

## 初始化

本骨架只含 `pubspec.yaml` 與 `lib/`。第一次開發前先產生 Android 平台檔案：

```bash
cd app
flutter create --platforms android --project-name ai_elder_care .
flutter pub get
```

## 結構

```
lib/
├── main.dart                 # 進入點：登入 → 依角色切換模式
├── elder/screens/            # 長者模式：語音對話、今日行程
├── caregiver/screens/        # 照護者模式：長者管理、每日摘要、統計圖表、事件時間軸
└── shared/services/          # api_client / auth / speech / audio / notification
```

API 規格見 [docs/api.md](../docs/api.md)。
