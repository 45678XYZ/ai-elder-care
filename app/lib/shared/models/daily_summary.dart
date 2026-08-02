import 'session_close.dart';

/// 每日摘要。欄位規格見 docs/api.md（GET /summaries）。
class DailySummary {
  const DailySummary({
    required this.elderId,
    required this.date,
    this.overview,
    required this.sections,
    required this.routines,
    this.alerts = const [],
    this.interactionCount = 0,
    this.dataStatus = SummaryDataStatus.partial,
    this.pendingSessionCount = 0,
    this.generatedAt,
  });

  final String elderId;

  /// 日期 YYYY-MM-DD。
  final String date;
  final String? overview;
  final SummarySections sections;
  final SummaryRoutines routines;

  /// 注意事項。
  final List<String> alerts;

  /// 對話輪數（`/chat` turn 數，不是 session 數）。
  final int interactionCount;

  /// 資料完整度；可用值見 [SummaryDataStatus]。為 partial 時本摘要不涵蓋當日全部對話。
  final String dataStatus;

  /// 尚未收斂的 session 數（仍在進行，或已關閉但批次未完成）。
  final int pendingSessionCount;

  final DateTime? generatedAt;

  /// 摘要仍不完整——UI 應提示照護者「還有 N 段對話整理中」，
  /// 避免把 partial 摘要當成當日全貌。
  bool get isPartial => dataStatus != SummaryDataStatus.complete;

  factory DailySummary.fromJson(Map<String, dynamic> json) => DailySummary(
        elderId: json['elder_id'] as String? ?? '',
        date: json['date'] as String? ?? '',
        overview: json['overview'] as String?,
        sections: SummarySections.fromJson(
            json['sections'] as Map<String, dynamic>? ?? const {}),
        routines: SummaryRoutines.fromJson(
            json['routines'] as Map<String, dynamic>? ?? const {}),
        alerts: (json['alerts'] as List<dynamic>?)
                ?.map((e) => e as String)
                .toList() ??
            const [],
        interactionCount: json['interaction_count'] as int? ?? 0,
        dataStatus: json['data_status'] as String? ?? SummaryDataStatus.partial,
        pendingSessionCount: json['pending_session_count'] as int? ?? 0,
        generatedAt: json['generated_at'] == null
            ? null
            : DateTime.tryParse(json['generated_at'] as String)?.toLocal(),
      );
}

/// 摘要分類；類別定義與 null 的呈現規則見 docs/api.md。
///
/// 欄位順序與 api.md 的七類（`EventType`）一致，也就是摘要頁的呈現順序。
class SummarySections {
  const SummarySections({
    this.diet,
    this.activity,
    this.sleep,
    this.medication,
    this.wellbeing,
    this.safety,
    this.other,
  });

  final String? diet;
  final String? activity;
  final String? sleep;
  final String? medication;
  final String? wellbeing;

  /// 跌倒、走失、詐騙、居家危害等安全事件；與 [DailySummary.alerts] 語意一致。
  final String? safety;
  final String? other;

  factory SummarySections.fromJson(Map<String, dynamic> json) =>
      SummarySections(
        diet: json['diet'] as String?,
        activity: json['activity'] as String?,
        sleep: json['sleep'] as String?,
        medication: json['medication'] as String?,
        wellbeing: json['wellbeing'] as String?,
        safety: json['safety'] as String?,
        other: json['other'] as String?,
      );
}

/// 摘要中的例行公事完成統計。
class SummaryRoutines {
  const SummaryRoutines({
    this.completed = 0,
    this.missed = 0,
    this.items = const [],
  });

  final int completed;
  final int missed;
  final List<SummaryRoutineItem> items;

  factory SummaryRoutines.fromJson(Map<String, dynamic> json) =>
      SummaryRoutines(
        completed: json['completed'] as int? ?? 0,
        missed: json['missed'] as int? ?? 0,
        items: (json['items'] as List<dynamic>?)
                ?.map((e) =>
                    SummaryRoutineItem.fromJson(e as Map<String, dynamic>))
                .toList() ??
            const [],
      );
}

class SummaryRoutineItem {
  const SummaryRoutineItem({
    required this.routineId,
    required this.title,
    required this.status,
  });

  final String routineId;
  final String title;

  /// 狀態；可用值見 docs/api.md。
  final String status;

  factory SummaryRoutineItem.fromJson(Map<String, dynamic> json) =>
      SummaryRoutineItem(
        routineId: json['routine_id'] as String? ?? '',
        title: json['title'] as String? ?? '',
        status: json['status'] as String? ?? '',
      );
}
