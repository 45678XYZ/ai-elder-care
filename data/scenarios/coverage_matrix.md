# Tool 覆蓋矩陣

確認 4 個情境合計覆蓋全部 17 個 Agent tools。

| # | Tool | A1 晨間 | A2 緊急 | B1 行程管理 | B2 情緒陪伴 |
|---|---|:---:|:---:|:---:|:---:|
| 1 | get_today_routines | ✓ | | | |
| 2 | remind_pending_routines | ✓ | | | |
| 3 | complete_routine | ✓ | | | |
| 4 | uncomplete_routine | ✓ | | | |
| 5 | create_routine | | | ✓ | |
| 6 | update_routine | | | ✓ | |
| 7 | delete_routine | | | ✓ | |
| 8 | get_elder_profile | ✓ | | | ✓ |
| 9 | update_elder_profile | | ✓ | | ✓ |
| 10 | get_recent_events | | ✓ | | |
| 11 | get_events_by_time | | | ✓ | |
| 12 | get_daily_summaries | | | ✓ | |
| 13 | get_recent_conversations | | | | ✓ |
| 14 | notify_caregiver (emergency) | | ✓ | | |
| 15 | notify_caregiver (critical_escalation) | | ✓ | | |
| 16 | notify_caregiver (mitigation) | | ✓ | | |
| 17 | search_health_knowledge | | ✓ | | ✓ |
| 18 | get_weather_forecast | ✓ | | | |
| 19 | web_search | | | | ✓ |

> `notify_caregiver` 是一個工具搭配 5 個 category，其中 emergency / critical_escalation / mitigation 在 A2 覆蓋；routine 和 summary 屬系統排程觸發，將在照護者場景中驗證。

**統計**：A1=6, A2=5(+notify×3), B1=5, B2=5 → 17 tools 全數覆蓋 ✓
