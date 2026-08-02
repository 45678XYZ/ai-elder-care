/// 生活事件。欄位規格見 docs/api.md（GET /events）。
class LifeEvent {
  const LifeEvent({
    required this.eventId,
    required this.elderId,
    required this.ts,
    required this.type,
    required this.detail,
    required this.source,
    this.conversationId,
    this.routineId,
  });

  final String eventId;
  final String elderId;

  /// 事件發生時間，已轉成裝置本地時區（見 [LifeEvent.fromJson]）。
  final DateTime ts;

  /// 分類；可用值見 docs/api.md。
  final String type;
  final String detail;

  /// 事件來源；可用值見 docs/api.md。
  final String source;
  final String? conversationId;

  /// 對應某筆例行公事時才有。
  final String? routineId;

  factory LifeEvent.fromJson(Map<String, dynamic> json) => LifeEvent(
        eventId: json['event_id'] as String? ?? '',
        elderId: json['elder_id'] as String? ?? '',
        // `.toLocal()` 不能省。api.md 的 `ts` 帶 `+08:00`，而 `DateTime.parse`
        // 遇到 offset 一律換算成 **UTC** 回傳（`isUtc == true`）。少了這一步，
        // 顯示端讀到的 `.hour`／`.day` 全是 UTC 值，時間軸上早上九點的事會標成
        // 半夜一點，日期分隔也跟著跨錯天。轉本地後才是使用者看到的牆上時間。
        ts: DateTime.tryParse(json['ts'] as String? ?? '')?.toLocal() ??
            DateTime.fromMillisecondsSinceEpoch(0),
        type: json['type'] as String? ?? 'other',
        detail: json['detail'] as String? ?? '',
        source: json['source'] as String? ?? '',
        conversationId: json['conversation_id'] as String?,
        routineId: json['routine_id'] as String?,
      );
}
