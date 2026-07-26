# 照護者模式 逐頁規則

共同約束見 [../../CLAUDE.md](../../CLAUDE.md)「照護者模式」段（可用標準 Material density、touch ≥48dp、閱讀文字放大）。四畫面目前為 `Placeholder()` TODO。

## 長者管理（elders_screen）
- GET/POST/PATCH `/elders`、GET/POST/PATCH `/routines`；兼管理後台
- 建例行事項（服藥時間、回診、約會）；巢狀底 `#faf6ee`
- **語音語言（`lang_preference`）只在這裡可改**——介面文字一律華語，這個值只決定長輩說話與聽回覆走華語或客語，長者端不提供切換（見 [../../CLAUDE.md](../../CLAUDE.md) 全域約束）

## 每日摘要（summaries_screen）
- GET `/summaries`：固定六類 sections，null 顯示「今日對話未提及」
- 手動觸發 POST `/summaries/generate`（Demo 用）
- 摘要日期塊底 `#f3ecdd`、圓角 12

## 統計圖表（stats_screen）
- GET `/stats`：今日互動、期間互動與活躍天數、例行公事逐項完成、daily 趨勢圖
- 圖表配色用 MASTER.md 事件分類色（飲食／活動／睡眠／身心／其他）

## 事件時間軸（timeline_screen）
- GET `/events`：type 六類過濾、`next_token` 分頁
- 圓點外環 `0 0 0 1.5px <dot色>`，dot 色依事件分類
