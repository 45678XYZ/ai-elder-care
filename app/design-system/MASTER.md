# 設計系統 — 暖紙手帳（長照陪伴）

方向定案：**1b 暖紙手帳**——紙感底、朱紅點綴、Noto Serif TC 襯線、農民曆牌面、極簡。
本檔為 design token 的**單一真實來源**（純數值，非 CSS）。改任何 `lib/theme/` 值之前先回這裡對。

> 來源：Claude Design 原型「長照陪伴原型 手帳」。Flutter 端轉換規則見 [../CLAUDE.md](../CLAUDE.md) 與計畫書 Phase 3。
> 顏色部分 accent／語意底以 `oklch()` 定義——**Flutter 需轉為 RGB/hex，轉完用工具核對色差**（見 CLAUDE.md 無障礙段）。

---

## 顏色 Colors

### 核心中性／紙感（Neutrals / paper）

| 用途 | 值 |
|---|---|
| 頁面底 body | `#e4dccb` |
| App 底 | `#f3ecdd` |
| 卡片主色 | `#fbf7ec` |
| 卡片替代底（虛線卡／預告） | `#fffdf8` |
| 淺色分隔／膠囊底 | `#efe8db` |
| 巢狀底（例行公事／摘要） | `#faf6ee` |
| 極淺格線底 | `#f2ece1` |
| 進度條軌道 | `#f2ece1` |
| 摘要日期塊底 | `#f3ecdd` |

### 文字 Text

| 用途 | 值 |
|---|---|
| 主文字（深咖） | `#33291f` |
| 次要文字 | `#5a4c3c` |
| 輔助／標籤灰咖 | `#a3927d` |
| 更淺輔助 | `#b8a88f` |
| 淺咖（次標題） | `#8a7c6d` |
| 雪佛龍／箭頭 | `#c9b8a3` |
| 深色列／導覽底 | `#33291f` |
| 深色列上文字 | `#f5ead9` |
| 深色列次文字 | `#c9b8a3` |

### 主色 Accent（紅／朱，以 oklch 定義）

| 用途 | 值 |
|---|---|
| 主 accent | `oklch(0.62 0.16 32)` |
| accent 深（hover） | `oklch(0.55 0.16 32)` |
| accent 最深（農民曆數字／宜） | `oklch(0.52 0.16 32)` |
| 連結預設 | `oklch(0.55 0.16 32)` |
| 連結 hover | `oklch(0.45 0.16 32)` |

### 語意色 Semantic（標籤／圖表）

字色與底色成對出現；底色一律 `oklch(0.95 …)` 高明度、低彩度以維持紙感。

| 用途 | 字 | 底 |
|---|---|---|
| 完成綠 | `#5a8a5f` | `oklch(0.95 0.04 145)` |
| 警示橘 | `#a3652f` | `oklch(0.95 0.05 70)` |
| 藍 | `#5a6f8a` | `oklch(0.95 0.03 250)` |

事件分類色（時間軸／統計，點色／字色／底色）：

| 類別 | 點 | 字 | 底 |
|---|---|---|---|
| 飲食 | `oklch(0.62 0.14 90)` | `#8a7324` | `oklch(0.95 0.05 90)` |
| 活動 | `oklch(0.6 0.12 145)` | `#4a7a50` | `oklch(0.95 0.04 145)` |
| 睡眠 | `oklch(0.55 0.09 280)` | `#5f5a8a` | `oklch(0.95 0.03 280)` |
| 身心 | `oklch(0.6 0.13 25)` | `#a34a3f` | `oklch(0.95 0.04 25)` |
| 其他 | `#8a7c6d` | `#6f6354` | `#f2ece1` |

其他：

| 用途 | 值 |
|---|---|
| 頭像黃底 | `oklch(0.95 0.05 80)` |
| 忌 圓標底 | `#8a7c6d` |
| 邊框／輸入框線 | `#e5dccb` |
| 虛線邊框 | `#d9c9b2` |

---

## 字體 Typography

- **字體**：Noto Serif TC → fallback `system-ui, sans-serif`
- **字重**：400 · 500 · 700 · 900
- **行高**：`.78`（農民曆數字） · `1` · `1.5` · `1.55` · `1.6`
- **字距**：`.12em` · `.15em`（分區標題）

### 字級 Font sizes（px）

`11 · 12 · 13 · 14 · 15 · 15.5 · 16 · 16.5 · 17 · 18 · 19 · 20 · 21 · 22 · 23 · 24 · 30 · 32 · 36 · 46 · 66`

> 此級距**橫跨長者與照護者兩模式**。11–15 為小標籤／輔助文字（多用於照護者密集資訊）；長者模式主要內文的硬性下限見 [../CLAUDE.md](../CLAUDE.md)（≥24），麥克風狀態等大數字走 46/66。

### 農民曆牌面 — 獨立字級組

牌面是單一視覺單元，六個元素的字級是一組互相依賴的比例，**不套用上面的通用級距**。值來自設計 v3 原檔，實作於 `lib/theme/app_theme.dart` 的 `AlmanacTypography`：

| 元素 | 字級 | 字重 | 其他 |
|---|---|---|---|
| 年份「2026」 | 28 | 900 | |
| 干支「歲次丙午年」 | 24 | 900 | |
| 月份數字「7」 | 40 | 900 | |
| 「月」 | 24 | 900 | |
| 農曆直排「農曆六月初六」 | 30 | 900 | 字距 8（直排＝字與字的**垂直間隔**，非 letterSpacing） |
| 大日期「19」 | 200 | 900 | 行高 0.72，以 `FittedBox(scaleDown)` 包住，窄螢幕或系統放大字級時自動縮 |
| 星期「星期日」 | 42 | 900 | 字距 16（v3 原檔為 400，實機看與其他元素不一致，改齊 900） |

單色，靠字級與位置分層次，不加第二個顏色。顏色照台灣日曆慣例：假日朱紅
（`accentText`）、平日藍（`calendarWeekday`）。

**表上的數值是牌面內寬 326（390 螢幕扣掉頁面 16 邊距與卡片 16 內距）時的渲染尺寸**，
不是絕對字級。牌面出現在三個尺寸——首頁小卡、撕曆過場、放大檢視——**版面完全相同**，
共用 `lib/elder/widgets/almanac_face.dart` 的 `AlmanacFace`，整組乘上 `牌面內寬 / 326`：

- 版面規則：頂列貼上緣（年靠左、歲次干支置中、月份在右上角，數字大、「月」在下）、
  農曆直排貼左緣、星期貼下緣；中段（直排右緣→右邊界、頂列下緣→星期上緣）
  整片是大日期的
- 大日期不照表縮，而是**撐滿中段**並置中。牌面有多大字就有多大，留白等於浪費長輩看得到的字級
- 農曆直排寫「五月廿六日」，不寫「農曆」二字（撕曆的寫法）；直排會跟著中段高度撐開，
  字距最多到 0.6 字高，再高就整欄置中
- 牌面比參考比例更高時（放大到整個直式螢幕），整組字跟著長，否則中間會空一塊；
  頂列與星期是橫的一行，成長上限分別是 1.25 與 1.45 倍——再大就會頂到左右兩邊
- 首頁小卡是半版寬，等比縮會掉到 8sp，因此頂列 12、農曆 11、星期 15 是下限
  （只影響字級，不影響版面）
- 牌面不跟隨系統字級縮放：字級已由牌面寬度決定，再乘一次只會爆版

放大檢視（日曆與早安圖）**按原比例**放到畫面放得下的最大：日曆照小卡那一面的
長寬比，早安圖照原圖比例（`contain`）。不拉伸填滿——拉伸會把同一份內容變成另一種
版面，放大的意思是同一張看得更清楚。內容與 64dp 關閉鈕**整組置中**，關閉鈕貼在
內容正下方，不釘在畫面最底。

---

## 間距 Spacing（px）

- **Gap**：`2 · 6 · 7 · 8 · 9 · 10 · 11 · 12 · 13 · 14 · 15 · 16`
- **Padding**：`3 · 3×8 · 4×11 · 5×12 · 7×14 · 7×11 · 10×12 · 11×20 · 12×16 · 13×15 · 13 · 14 · 15×17 · 16 · 20 · 20×22×18`
- **Margin（卡片外距）**：`14×18×0 · 14×18`
- **頁面 body padding**：`32×16`

---

## 圓角 Border-radius（px）

| 用途 | 值 |
|---|---|
| 小標籤／badge | 8 |
| 進度條 | 5 |
| 圖示方塊 | 11 · 12 |
| 輸入框 | 11 |
| 導覽鈕 | 13 · 14 |
| 摘要日期塊 | 12 |
| 卡片 | 14 · 16 · 18 · 20 |
| Logo 方塊 | 26 |
| 氣泡 AI | 18 18 18 4 |
| 氣泡 長者 | 18 18 4 18 |
| 底部語音面板 | 26 26 0 0 |
| 膠囊／開關／toast | 999（全圓） |
| 圓形頭像／麥克風／圓標 | 50% |

---

## 陰影 Shadows

| 用途 | 值 |
|---|---|
| 卡片（淺） | `0 2px 8px rgba(90,70,50,.06)` |
| 卡片（中） | `0 2px 10px rgba(90,70,50,.07)` / `0 3px 12px rgba(90,70,50,.06)` / `0 3px 12px rgba(90,70,50,.08)` |
| 氣泡 | `0 2px 8px rgba(90,70,50,.07)` |
| Logo | `0 8px 24px oklch(0.62 0.16 32 / .3)` |
| 麥克風鈕 | `0 6px 18px oklch(0.62 0.16 32 / .4)` |
| 語音面板（上緣） | `0 -4px 20px rgba(61,50,41,.07)` |
| Toast／深色浮層 | `0 6px 20px rgba(0,0,0,.25)` |
| 開關把手 | `0 1px 4px rgba(0,0,0,.25)` |
| 時間軸圓點外環 | `0 0 0 1.5px <dot色>` |

> Flutter 轉換：CSS `box-shadow` 的 blur 與 Flutter `BoxShadow.blurRadius` 定義不同，**不要直接照抄數值**，轉完目視微調（計畫書 Phase 3 註記）。

---

# 設計原則（UIPM 萃取）

以下為自 `ui-ux-pro-max-skill` 萃取、過三道閘門後併入的規則（決策紀錄與被拒項見 uipm-harvest/ACCEPTED.md）。
硬性數值仍以本檔上半與 [../CLAUDE.md](../CLAUDE.md) 為準；本節只補「怎麼做」的原則，均已翻成 Flutter 概念。
每條標來源；閘門：A=與硬約束衝突｜B=價值判斷非可測｜C=Web 慣例無 Flutter 對應。

## 6. 無障礙與對比（承 CLAUDE.md）

- 狀態不可只靠顏色：一律 **顏色＋icon＋文字**，避免紅綠單色（色盲）
  <!-- src: uipm -d ux/-d style, 2026-07-24 -->
- 錯誤與重要狀態變化須可被螢幕報讀器朗讀：`SemanticsService.announce(...)` 或 liveRegion `Semantics`
  <!-- src: uipm -d ux (aria-live→Flutter), 2026-07-24, 閘門 C 已翻譯 -->
- icon-only 按鈕須有無障礙名稱：`Semantics(label:)` 或 `IconButton(tooltip:)`
  <!-- src: uipm -d ux + -s flutter, 2026-07-24 -->
- 有意義的圖片給語意描述 `Semantics(label:)`；純裝飾圖不給
  <!-- src: uipm -d ux (alt text→Flutter), 2026-07-24 -->
- 觸覺回饋：重要確認（如例行公事完成）用 `HapticFeedback`，勿每次點擊都震
  <!-- src: uipm -d ux touch + -d style inclusive, 2026-07-24 -->
- 對比維持 **7:1（AAA）**，凡 uipm 出現的 4.5:1/AA 一律不採（閘門 A）

## 7. 觸控與間距（承 CLAUDE.md 觸控段）

- 相鄰觸控目標間距 **≥8dp**
  <!-- src: uipm -d ux (touch spacing), 2026-07-24 -->
- 主要內容避免水平 swipe（與系統手勢衝突；長者模式本就禁隱藏手勢）
  <!-- src: uipm -d ux (gesture conflicts), 2026-07-24 -->
- 觸控目標尺寸仍以 CLAUDE.md 為準（elder 60dp／caregiver 48dp），不採 uipm 的 44px（閘門 A）

## 8. 表單與錯誤（照護者模式）

- 失焦即驗證（on blur），不要只在送出時驗證
  <!-- src: uipm -d ux (inline validation), 2026-07-24 -->
- 錯誤訊息顯示在**對應欄位下方**，不集中在頁首
  <!-- src: uipm -d ux (error placement), 2026-07-24 -->
- 錯誤要有復原路徑（明確下一步／重試按鈕）
  <!-- src: uipm -d ux (error recovery), 2026-07-24 -->
- 送出要有狀態回饋：loading → success／error，不可無回應
  <!-- src: uipm -d ux (submit feedback), 2026-07-24 -->
- 非同步畫面**三態都要畫**：loading／error／success，不只畫成功
  <!-- src: uipm -s flutter (async states), 2026-07-24 -->

## 9. 語音互動模式（強化 pages/elder-mode.md）

- 四狀態（idle／listening／thinking／speaking）**每一態都要有明顯且不同的視覺回饋**：狀態文字＋大圖示＋顏色，不只靠顏色
  <!-- src: uipm -d style (voice-first / zero-ui 原則), 2026-07-24 -->
- **永遠提供備援 UI**（打字輸入）；語音辨識失敗可退回
  <!-- src: uipm -d style (voice: fallback UI provided), 2026-07-24 -->
- 語音是「替代路徑」**不是「隱藏控制」**：控制項保持可見、可觸，不做 hover 揭露／漸進隱藏
  <!-- src: uipm -d style (對 zero-interface 反向採用), 2026-07-24 -->

## 10. Flutter 實作對應

- 字級走 `Theme.of(context).textTheme`，不寫死 `fontSize`；支援 `textScaleFactor`
  <!-- src: uipm -s flutter (theme typography), 2026-07-24 -->
- 顏色走 `ThemeData`／`AppColors`，不 hardcode（承 CLAUDE.md）
  <!-- src: uipm -s flutter (ThemeData), 2026-07-24 -->
- 版面用 `Expanded`／`Flexible`／`LayoutBuilder`，不寫死尺寸——直接支撐 `textScaler 2.0` 不 overflow
  <!-- src: uipm -s flutter (layout responsive), 2026-07-24 -->
- 間距用 `SizedBox` ＋ `AppSpacing` 常數（不用 `Container` 只為留白）
  <!-- src: uipm -s flutter (SizedBox spacing), 2026-07-24 -->
- 長列表用 `ListView.builder`（照護者時間軸／摘要）
  <!-- src: uipm -s flutter (ListView.builder), 2026-07-24 -->
- 有狀態列表項給 `Key(ValueKey(id))`
  <!-- src: uipm -s flutter (list keys), 2026-07-24 -->
- `Controller`／訂閱在 `dispose()` 釋放
  <!-- src: uipm -s flutter (dispose), 2026-07-24 -->
- 不在 `build()` 呼叫 `setState`；`setState` 只放 UI 狀態，商業邏輯不放
  <!-- src: uipm -s flutter (setState hygiene), 2026-07-24 -->
- 非同步用 `FutureBuilder`／`StreamBuilder`，不用 `setState` 接 async；`StreamSubscription` 在 `dispose()` 取消
  <!-- src: uipm -s flutter (async builders), 2026-07-24 (round 2) -->
- 導覽用具名路由＋型別安全參數；照護者多畫面用 `GoRouter`
  <!-- src: uipm -s flutter (navigation), 2026-07-24 (round 2) -->
- Android 返回用 `PopScope`（`WillPopScope` 已棄用）
  <!-- src: uipm -s flutter (PopScope), 2026-07-24 -->
- widget 組合優先於繼承；避免過深巢狀（抽成 widget／method）
  <!-- src: uipm -s flutter (composition / nesting), 2026-07-24 -->
- 狀態管理沿用 CLAUDE.md（目前 `StatefulWidget`，引入框架前全隊確認）；不採 uipm「prefer Riverpod」建議（閘門 B）

## 11. 禁止事項——去除「AI 感」

暖紙手帳的反面清單。命中任一即打回。

- ❌ AI 紫／粉漸層（`#6366F1` 類）、聊天室 3-dot 打字動畫、打字機 streaming 特效當賣點
  <!-- src: uipm -ds AVOID + -d style AI-Native 反例, 2026-07-24 -->
- ❌ 小字 ＋ 複雜導覽
  <!-- src: uipm -ds AVOID, 2026-07-24 -->
- ❌ emoji 當功能圖示（用正式 icon 資產）
  <!-- src: uipm -ds checklist, 2026-07-24 -->
- ❌ 隱藏式控制／hover 揭露／手勢揭露（zero-interface 反例）——控制要看得見
  <!-- src: uipm -d style (zero-interface 反例), 2026-07-24 -->
- ❌ 套用外部風格建議（medical blue、Figtree、Brutalism…）——**一律維持暖紙手帳：襯線、紙感、朱紅點綴**
  <!-- src: uipm -ds/-d typography 建議一律不採, 2026-07-24 -->
- ❌ 圓餅圖用於無障礙優先情境（依賴顏色、色盲不友善，WCAG 評 C）——改用長條圖／Waffle
  <!-- src: uipm -d chart (pie a11y grade C), 2026-07-24 (round 2) -->

## 12. App 導覽與畫面狀態（照護者多畫面）

- 返回行為要可預測、保留畫面狀態；首次按返回**不可直接退出 App**（`PopScope` 處理）
  <!-- src: uipm -d web (back behavior, Critical), 2026-07-24 (round 2) -->
- Modal／bottom sheet 必有**明確關閉方式（關閉鈕，非只靠 swipe）**，不可困住使用者
  <!-- src: uipm -d web (modal escape), 2026-07-24 (round 2) -->
- 切回畫面要還原捲動位置與表單輸入，不每次重置
  <!-- src: uipm -d web (preserve screen state), 2026-07-24 (round 2) -->
- 照護者底部 tab **≤5 個**（長者模式仍 ≤3 可互動元素，見 CLAUDE.md）
  <!-- src: uipm -d web (bottom tabs), 2026-07-24 (round 2) -->

## 13. 畫面狀態回饋

- **>300ms 的操作要有可見 loading**（進度指示／skeleton），不凍住畫面
  <!-- src: uipm -d web (loading indicators), 2026-07-24 (round 2) -->
- **成功操作要有簡短確認**（toast／勾選），不靜默完成——長者確認例行公事完成尤其要有明確回饋
  <!-- src: uipm -d web (success feedback), 2026-07-24 (round 2) -->
- 錯誤＝欄位層級訊息＋摘要提示，不只改邊框色（承第 8 節）
  <!-- src: uipm -d web (error feedback), 2026-07-24 (round 2) -->

## 14. 統計圖表選型（照護者 stats 畫面）

圖表庫與配色不採 uipm 的 Web 建議（Chart.js／#0080FF）；Flutter 端用 `fl_chart` 類套件＋本檔事件分類色。

- **趨勢隨時間 → 折線圖**；資料點 <4 改用**數字卡**不畫圖；系列 >6 太雜
  <!-- src: uipm -d chart (trend over time), 2026-07-24 (round 2) -->
- **比較類別／例行公事逐項完成 → 長條圖**，由大到小排序、每條標數值，類別 ≤15
  <!-- src: uipm -d chart (compare categories), 2026-07-24 (round 2) -->
- **百分比／完成率 → Waffle 格子圖**（無障礙優於圓餅），%文字恆顯示
  <!-- src: uipm -d chart (waffle vs pie), 2026-07-24 (round 2) -->
- 多系列用**線型（實線／虛線／點線）或圖案**區分，不只靠顏色（呼應事件分類色）
  <!-- src: uipm -d chart (differentiate by line style), 2026-07-24 (round 2) -->
- 一律提供**數值備援**：數值標籤＋可切換的資料表
  <!-- src: uipm -d chart (a11y fallback), 2026-07-24 (round 2) -->
