# 畫面截圖

**設計原型的截圖一張都沒有進版控**——Claude Design 原型「長照陪伴原型 手帳」只留在該工具裡。
本目錄實際只有 [built/](built/)。

規格的唯一來源是 [../MASTER.md](../MASTER.md) 的 token 與 [../pages/](../pages/) 的逐頁規則；
`built/` 是現況紀錄，**不是規格**。兩者不一致時以 MASTER.md 為準。

## `built/` — 實作出來的畫面

目前 App 實際跑出來的樣子（390×844）：

**認證**（長者與照護者共用，登入後才由 token 分流）
- `auth-sign-in.png`（登入）
- `auth-sign-up.png`（註冊）
- `auth-verify.png`（信箱驗證碼）

**長者模式**
- `elder-today.png`（今日：農民曆牌面 + 行程）
- `elder-calendar-enlarged.png`（點開日曆的放大檢視：同一套牌面、同比例放大）
- `elder-today-link-entry.png`（同頁捲到底，未連結家人時的入口）
- `elder-chat-idle.png`（聊天待機）
- `elder-link-caregiver.png` / `elder-link-caregiver-done.png`（連結家人：初始與連結成功）

**照護者模式**
- `caregiver-summaries.png` / `caregiver-timeline.png` / `caregiver-stats.png` / `caregiver-elders.png`

**共用**
- `setup.png`（初次設定）、`role-select.png`（角色選擇）

### 產生方式

```
flutter build web --release
# 靜態 server 提供 build/web，再用能明確指定視窗大小的 headless 瀏覽器逐一截圖
```

沒有 Playwright 也可以：用 `chrome --headless=new --window-size=390,844
--remote-debugging-port=9222` 起一個瀏覽器，再用 CDP（`Page.navigate` →
`Page.captureScreenshot`）截。狀態直接寫 localStorage 佈好——`shared_preferences`
在 web 上就是 localStorage，key 前面加 `flutter.`、值是 JSON
（例：`localStorage.setItem('flutter.linked_caregiver_ids', '["care-1"]')`）。

幾個踩過的雷：

- `flutter run -d web-server` 的 debug build 起不來（DDC 載完但 engine 不啟動，畫面全白），**要用 release build**。
- 截圖需強制軟體渲染（`--use-gl=swiftshader --disable-gpu-compositing`），否則 WebGL canvas 截出來是空白。
- **視窗大小要在啟動時就給**（`--window-size=390,844`）。`Emulation.setDeviceMetricsOverride` 對 Flutter 的 canvas 無效，會截到一個尺寸不對的畫面。
- 只有 hash 不同的網址**不會重新載入**，會拿到上一輪的畫面與舊 build。先導到 `about:blank` 再導回去。
- 字體是 google_fonts 執行期下載的，截太早會看到系統預設字或缺字方框——載入後至少等 6 秒。
- 進到今日畫面會播 2.2 秒的撕曆過場，要等它播完再截（或反過來，趁那段時間截過場本身）。
- 需要前一頁帶狀態的畫面（如驗證碼頁的信箱）得走完流程才截得到，直接開網址會被導走。
- 首次設定與連結家人的狀態存在 localStorage，每一組截圖前先 `localStorage.clear()`，否則入口卡會消失。

### 桌機上看手機比例

`app/web/phone.html` 是開發用的預覽外框，把 App 裝進手機尺寸的 iframe，可切畫面與尺寸（360×800／412×915／390×844／320×700）。`flutter build web` 會自動帶進 `build/web`。
