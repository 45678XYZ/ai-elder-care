import 'package:flutter_local_notifications/flutter_local_notifications.dart';
import 'package:flutter_timezone/flutter_timezone.dart';
import 'package:timezone/data/latest_all.dart' as tz_data;
import 'package:timezone/timezone.dart' as tz;

import '../models/routine.dart';

/// 本地通知——例行公事提醒。
///
/// 長者端依 `GET /routines` 的定義排本地通知；`/chat` 回 `routines_updated=true`
/// 或照護者在管理頁改了行程之後，重拉並呼叫 [syncRoutines] 重排。
///
/// 為什麼是**本地**通知而不是推播：提醒的時間點在 App 端就算得出來（routine 的 schedule
/// 是固定的），不需要後端在對的時刻叫醒裝置。這也表示手機離線、後端掛掉時提醒照樣會響。
///
/// 排程一律用**帶時區的時間**（台灣 +08:00）。用本地 DateTime 排，跨時區或日光節約
/// 變動時系統會把提醒排到錯誤的時刻。
class NotificationService {
  NotificationService._();
  static final NotificationService instance = NotificationService._();

  final _plugin = FlutterLocalNotificationsPlugin();
  bool _ready = false;

  /// Android 通知頻道。長輩的提醒不該被系統降級或延後，所以用最高重要度。
  static const _channel = AndroidNotificationChannel(
    'routine_reminders',
    '例行公事提醒',
    description: '吃藥、量血壓、回診等排定事項的提醒',
    importance: Importance.max,
  );

  /// 初始化時區資料與外掛。App 啟動時呼叫一次。
  Future<void> init() async {
    if (_ready) return;

    tz_data.initializeTimeZones();
    tz.setLocalLocation(tz.getLocation(await _deviceTimezone()));

    await _plugin.initialize(
      settings: const InitializationSettings(
        android: AndroidInitializationSettings('@mipmap/ic_launcher'),
      ),
    );
    await _plugin
        .resolvePlatformSpecificImplementation<
            AndroidFlutterLocalNotificationsPlugin>()
        ?.createNotificationChannel(_channel);

    _ready = true;
  }

  /// 取裝置時區；取不到就退回台北——這個 App 的使用者都在台灣，
  /// 讓提醒排在 +08:00 比因為拿不到時區而整個排錯要好。
  Future<String> _deviceTimezone() async {
    try {
      final tzInfo = await FlutterTimezone.getLocalTimezone();
      final name = tzInfo.identifier;
      return name.isEmpty ? 'Asia/Taipei' : name;
    } catch (_) {
      return 'Asia/Taipei';
    }
  }

  /// 請求通知權限（Android 13+ 才需要；更早的版本安裝即授權）。
  ///
  /// 回傳 false 表示使用者拒絕——此時提醒不會響，UI 應該讓照護者知道。
  Future<bool> requestPermission() async {
    final android = _plugin.resolvePlatformSpecificImplementation<
        AndroidFlutterLocalNotificationsPlugin>();
    if (android == null) return false;
    return await android.requestNotificationsPermission() ?? false;
  }

  /// 依目前的 routine 定義重排全部提醒。
  ///
  /// 先清空再重排，而不是逐筆比對差異：routine 可能被停用、改時間或刪除，
  /// 全量重來最不容易漏掉舊的殘留提醒，而數量本來就只有個位數。
  ///
  /// 只排 `active` 且 `remind` 的項目；`once` 型且時間已過的不排。
  Future<void> syncRoutines(List<Routine> routines) async {
    if (!_ready) await init();
    await _plugin.cancelAll();

    final now = tz.TZDateTime.now(tz.local);
    for (final r in routines) {
      if (!r.active || !r.remind) continue;
      final at = nextOccurrence(r.schedule, now);
      if (at == null) continue;
      await _schedule(r, at);
    }
  }

  Future<void> _schedule(Routine routine, tz.TZDateTime at) async {
    await _plugin.zonedSchedule(
      id: notificationId(routine.routineId),
      title: routine.title,
      body: '${_hhmm(at)} 的提醒，做完可以在「今日」確認',
      scheduledDate: at,
      notificationDetails: NotificationDetails(
        android: AndroidNotificationDetails(
          _channel.id,
          _channel.name,
          channelDescription: _channel.description,
          importance: Importance.max,
          priority: Priority.high,
          // 長輩可能沒看著手機，聲音與震動都要
          playSound: true,
          enableVibration: true,
        ),
      ),
      // 準時觸發：吃藥提醒晚 15 分鐘沒有意義
      androidScheduleMode: AndroidScheduleMode.exactAllowWhileIdle,
      // daily/weekly 靠這個參數重複；once 不給就是單次
      matchDateTimeComponents: _repeatRule(routine.schedule.freq),
    );
  }

  DateTimeComponents? _repeatRule(String freq) => switch (freq) {
        'daily' => DateTimeComponents.time,
        'weekly' => DateTimeComponents.dayOfWeekAndTime,
        _ => null,
      };

  /// 取消全部提醒（登出、切換長輩時用）。
  Future<void> cancelAll() async {
    if (!_ready) await init();
    await _plugin.cancelAll();
  }

  /// 目前已排定的提醒，供除錯與「提醒是否真的排上了」的檢查。
  Future<List<PendingNotificationRequest>> pending() async {
    if (!_ready) await init();
    return _plugin.pendingNotificationRequests();
  }

  static String _hhmm(tz.TZDateTime t) =>
      '${t.hour.toString().padLeft(2, '0')}:${t.minute.toString().padLeft(2, '0')}';
}

/// routine ID（字串）對應到通知 ID（int）。
///
/// 取 hashCode 的正值：同一個 routine 每次都得到同一個通知 ID，重排時會覆蓋自己
/// 而不是疊出第二則提醒。
int notificationId(String routineId) => routineId.hashCode & 0x7fffffff;

/// 算出這個排程的下一次觸發時間；已經沒有下一次（過期的 once）時回 null。
///
/// 抽成純函式是為了測得到——排程算錯只會表現成「提醒沒響」或「半夜響」，
/// 那是在真機上等一整天才發現得了的 bug。
///
/// 規則：
/// - `daily`：今天的該時刻，已過就明天
/// - `weekly`：本週該星期幾的該時刻，已過就下週
/// - `once`：指定日期時刻，已過回 null
tz.TZDateTime? nextOccurrence(RoutineSchedule schedule, tz.TZDateTime now) {
  final time = _parseHhmm(schedule.time);
  if (time == null) return null;

  switch (schedule.freq) {
    case 'daily':
      final today = tz.TZDateTime(
          tz.local, now.year, now.month, now.day, time.$1, time.$2);
      return today.isAfter(now) ? today : today.add(const Duration(days: 1));

    case 'weekly':
      final weekday = schedule.weekday;
      if (weekday == null || weekday < 1 || weekday > 7) return null;
      // DateTime.weekday 也是週一=1，與 api.md 的定義一致
      var candidate = tz.TZDateTime(
          tz.local, now.year, now.month, now.day, time.$1, time.$2);
      final delta = (weekday - candidate.weekday) % 7;
      candidate = candidate.add(Duration(days: delta));
      return candidate.isAfter(now)
          ? candidate
          : candidate.add(const Duration(days: 7));

    case 'once':
      final date = _parseDate(schedule.date);
      if (date == null) return null;
      final at =
          tz.TZDateTime(tz.local, date.$1, date.$2, date.$3, time.$1, time.$2);
      return at.isAfter(now) ? at : null;

    default:
      return null;
  }
}

/// "09:00" → (9, 0)；格式不對回 null。
(int, int)? _parseHhmm(String? v) {
  if (v == null) return null;
  final parts = v.split(':');
  if (parts.length != 2) return null;
  final h = int.tryParse(parts[0]);
  final m = int.tryParse(parts[1]);
  if (h == null || m == null || h < 0 || h > 23 || m < 0 || m > 59) return null;
  return (h, m);
}

/// "2026-07-26" → (2026, 7, 26)；格式不對回 null。
(int, int, int)? _parseDate(String? v) {
  if (v == null) return null;
  final parts = v.split('-');
  if (parts.length != 3) return null;
  final y = int.tryParse(parts[0]);
  final mo = int.tryParse(parts[1]);
  final d = int.tryParse(parts[2]);
  if (y == null || mo == null || d == null) return null;
  return (y, mo, d);
}
