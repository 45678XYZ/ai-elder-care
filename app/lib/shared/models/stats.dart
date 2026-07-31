/// 統計。欄位規格見 docs/api.md（GET /stats）。
class Stats {
  const Stats({
    required this.elderId,
    required this.today,
    required this.period,
    this.byRoutine = const [],
    this.daily = const [],
  });

  final String elderId;
  final StatsToday today;
  final StatsPeriod period;

  /// 逐項例行公事完成統計。
  final List<RoutineStat> byRoutine;

  /// 逐日資料，供繪製趨勢圖。
  final List<DailyStat> daily;

  factory Stats.fromJson(Map<String, dynamic> json) => Stats(
        elderId: json['elder_id'] as String? ?? '',
        today: StatsToday.fromJson(
            json['today'] as Map<String, dynamic>? ?? const {}),
        period: StatsPeriod.fromJson(
            json['period'] as Map<String, dynamic>? ?? const {}),
        byRoutine: ((json['routines'] as Map<String, dynamic>?)?['by_routine']
                    as List<dynamic>?)
                ?.map((e) => RoutineStat.fromJson(e as Map<String, dynamic>))
                .toList() ??
            const [],
        daily: (json['daily'] as List<dynamic>?)
                ?.map((e) => DailyStat.fromJson(e as Map<String, dynamic>))
                .toList() ??
            const [],
      );
}

/// 今日即時統計。
class StatsToday {
  const StatsToday({
    this.interactionCount = 0,
    this.lastInteractionAt,
  });

  final int interactionCount;
  final DateTime? lastInteractionAt;

  factory StatsToday.fromJson(Map<String, dynamic> json) => StatsToday(
        interactionCount: json['interaction_count'] as int? ?? 0,
        lastInteractionAt: json['last_interaction_at'] == null
            ? null
            : DateTime.tryParse(json['last_interaction_at'] as String),
      );
}

/// 期間彙總統計。
class StatsPeriod {
  const StatsPeriod({
    this.days = 0,
    this.interactionCount = 0,
    this.activeDays = 0,
  });

  final int days;
  final int interactionCount;
  final int activeDays;

  factory StatsPeriod.fromJson(Map<String, dynamic> json) => StatsPeriod(
        days: json['days'] as int? ?? 0,
        interactionCount: json['interaction_count'] as int? ?? 0,
        activeDays: json['active_days'] as int? ?? 0,
      );
}

/// 單筆例行公事的期間完成統計。
class RoutineStat {
  const RoutineStat({
    required this.routineId,
    required this.title,
    this.completed = 0,
    this.total = 0,
  });

  final String routineId;
  final String title;
  final int completed;
  final int total;

  factory RoutineStat.fromJson(Map<String, dynamic> json) => RoutineStat(
        routineId: json['routine_id'] as String? ?? '',
        title: json['title'] as String? ?? '',
        completed: json['completed'] as int? ?? 0,
        total: json['total'] as int? ?? 0,
      );
}

/// 單日統計（趨勢圖用）。
class DailyStat {
  const DailyStat({
    required this.date,
    this.interactionCount = 0,
    this.routinesCompleted = 0,
    this.routinesTotal = 0,
  });

  final String date;
  final int interactionCount;
  final int routinesCompleted;
  final int routinesTotal;

  factory DailyStat.fromJson(Map<String, dynamic> json) => DailyStat(
        date: json['date'] as String? ?? '',
        interactionCount: json['interaction_count'] as int? ?? 0,
        routinesCompleted: json['routines_completed'] as int? ?? 0,
        routinesTotal: json['routines_total'] as int? ?? 0,
      );
}
