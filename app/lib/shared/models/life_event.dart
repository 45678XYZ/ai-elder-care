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

  /// 事件發生時間。
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
        ts: DateTime.tryParse(json['ts'] as String? ?? '') ??
            DateTime.fromMillisecondsSinceEpoch(0),
        type: json['type'] as String? ?? 'other',
        detail: json['detail'] as String? ?? '',
        source: json['source'] as String? ?? '',
        conversationId: json['conversation_id'] as String?,
        routineId: json['routine_id'] as String?,
      );
}
