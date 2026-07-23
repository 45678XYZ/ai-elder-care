/// 例行公事定義。欄位規格見 docs/api.md（GET /routines 定義列表）。
class Routine {
  const Routine({
    required this.routineId,
    required this.elderId,
    required this.title,
    required this.type,
    required this.schedule,
    this.remind = true,
    this.createdBy,
    this.active = true,
    this.createdAt,
  });

  final String routineId;
  final String elderId;
  final String title;

  /// 分類；可用值見 docs/api.md（與事件同一組）。
  final String type;
  final RoutineSchedule schedule;
  final bool remind;

  /// 建立來源；可用值見 docs/api.md。
  final String? createdBy;
  final bool active;
  final DateTime? createdAt;

  factory Routine.fromJson(Map<String, dynamic> json) => Routine(
        routineId: json['routine_id'] as String? ?? '',
        elderId: json['elder_id'] as String? ?? '',
        title: json['title'] as String? ?? '',
        type: json['type'] as String? ?? 'other',
        schedule: RoutineSchedule.fromJson(
            json['schedule'] as Map<String, dynamic>? ?? const {}),
        remind: json['remind'] as bool? ?? true,
        createdBy: json['created_by'] as String?,
        active: json['active'] as bool? ?? true,
        createdAt: json['created_at'] == null
            ? null
            : DateTime.tryParse(json['created_at'] as String),
      );
}

/// 排程；[freq] 的可用值與各自適用的欄位見 docs/api.md。
class RoutineSchedule {
  const RoutineSchedule({
    required this.freq,
    this.time,
    this.weekday,
    this.date,
  });

  final String freq;

  /// HH:mm。
  final String? time;

  /// weekly 用；值域見 docs/api.md。
  final int? weekday;

  /// once 用：YYYY-MM-DD。
  final String? date;

  factory RoutineSchedule.fromJson(Map<String, dynamic> json) =>
      RoutineSchedule(
        freq: json['freq'] as String? ?? 'daily',
        time: json['time'] as String?,
        weekday: json['weekday'] as int?,
        date: json['date'] as String?,
      );

  /// 建立/更新例行公事時送出（只帶該 freq 用得到的欄位）。
  Map<String, dynamic> toJson() => {
        'freq': freq,
        if (time != null) 'time': time,
        if (weekday != null) 'weekday': weekday,
        if (date != null) 'date': date,
      };
}

/// 當日行程視圖（GET /routines?date=）。
class DailyRoutineView {
  const DailyRoutineView({
    required this.date,
    this.items = const [],
  });

  final String date;
  final List<RoutineOccurrence> items;

  factory DailyRoutineView.fromJson(Map<String, dynamic> json) =>
      DailyRoutineView(
        date: json['date'] as String? ?? '',
        items: (json['items'] as List<dynamic>?)
                ?.map((e) =>
                    RoutineOccurrence.fromJson(e as Map<String, dynamic>))
                .toList() ??
            const [],
      );
}

/// 當日某筆行程的展開狀態（當日行程視圖的 item，或確認完成後回傳的 occurrence）。
class RoutineOccurrence {
  const RoutineOccurrence({
    required this.routineId,
    required this.title,
    required this.type,
    required this.scheduledAt,
    required this.status,
    this.completedAt,
    this.completedBy,
  });

  final String routineId;
  final String title;
  final String type;
  final DateTime scheduledAt;

  /// 狀態；可用值與判定規則見 docs/api.md。
  final String status;
  final DateTime? completedAt;

  /// 完成者；可用值見 docs/api.md。
  final String? completedBy;

  factory RoutineOccurrence.fromJson(Map<String, dynamic> json) =>
      RoutineOccurrence(
        routineId: json['routine_id'] as String? ?? '',
        title: json['title'] as String? ?? '',
        type: json['type'] as String? ?? 'other',
        scheduledAt: DateTime.tryParse(json['scheduled_at'] as String? ?? '') ??
            DateTime.fromMillisecondsSinceEpoch(0),
        status: json['status'] as String? ?? 'pending',
        completedAt: json['completed_at'] == null
            ? null
            : DateTime.tryParse(json['completed_at'] as String),
        completedBy: json['completed_by'] as String?,
      );
}
