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

  /// 對話輪數。
  final int interactionCount;
  final DateTime? generatedAt;

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
        generatedAt: json['generated_at'] == null
            ? null
            : DateTime.tryParse(json['generated_at'] as String),
      );
}

/// 摘要分類；類別定義與 null 的呈現規則見 docs/api.md。
class SummarySections {
  const SummarySections({
    this.diet,
    this.activity,
    this.sleep,
    this.medication,
    this.wellbeing,
    this.other,
  });

  final String? diet;
  final String? activity;
  final String? sleep;
  final String? medication;
  final String? wellbeing;
  final String? other;

  factory SummarySections.fromJson(Map<String, dynamic> json) =>
      SummarySections(
        diet: json['diet'] as String?,
        activity: json['activity'] as String?,
        sleep: json['sleep'] as String?,
        medication: json['medication'] as String?,
        wellbeing: json['wellbeing'] as String?,
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
