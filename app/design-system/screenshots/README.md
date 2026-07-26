# 畫面截圖

放 Claude Design 原型「長照陪伴原型 手帳」各頁截圖（PNG），實作對照用。建議檔名：

**長者模式**
- `elder-today.png`（今日：農民曆牌面 + 行程）
- `elder-chat-idle.png` / `elder-chat-listening.png` / `elder-chat-thinking.png` / `elder-chat-speaking.png`

**照護者模式**
- `caregiver-elders.png`（長者管理）
- `caregiver-summaries.png`（每日摘要）
- `caregiver-stats.png`（統計圖表）
- `caregiver-timeline.png`（事件時間軸）

> 截圖是「要格」不是「要照抄的 code」——實作以 [../MASTER.md](../MASTER.md) 的 token 為準，截圖只定版面與資訊層級。

## `built/` — 實作出來的畫面

本目錄下的 [built/](built/) 放的是**目前 App 實際跑出來的樣子**，與上面的設計原型分開：原型是規格來源，`built/` 是現況紀錄。兩者不一致時以原型與 MASTER.md 為準。

產生方式（390×844，iPhone 尺寸）：

```
flutter build web --release
# 用靜態 server 提供 build/web，再以 Chrome 逐一截圖各路由
```

注意 `flutter run -d web-server` 的 debug build 起不來（DDC 載完但 engine 不啟動，畫面全白），要用 release build；Chrome 截圖需強制軟體渲染（`--use-gl=swiftshader`），否則 WebGL canvas 截出來是空白。
