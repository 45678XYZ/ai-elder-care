import 'package:e_hakka_care/shared/models/routine.dart';
import 'package:e_hakka_care/shared/services/notification_service.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:timezone/data/latest_all.dart' as tz_data;
import 'package:timezone/timezone.dart' as tz;

/// 排程時間算錯，症狀是「提醒沒響」或「半夜響」——那是在真機上等一整天才發現得了的 bug。
/// 所以 [nextOccurrence] 抽成純函式，用固定的「現在」把每條規則釘住。
void main() {
  setUpAll(() {
    tz_data.initializeTimeZones();
    tz.setLocalLocation(tz.getLocation('Asia/Taipei'));
  });

  /// 2026-07-26 是星期日，取當天 10:30 當作「現在」。
  tz.TZDateTime now() => tz.TZDateTime(tz.local, 2026, 7, 26, 10, 30);

  group('每天', () {
    test('今天還沒到就排今天', () {
      final at = nextOccurrence(
          const RoutineSchedule(freq: 'daily', time: '19:00'), now());

      expect(at, isNotNull);
      expect(at!.day, 26);
      expect(at.hour, 19);
      expect(at.minute, 0);
    });

    test('今天已經過了就排明天', () {
      final at = nextOccurrence(
          const RoutineSchedule(freq: 'daily', time: '09:00'), now());

      expect(at!.day, 27);
      expect(at.hour, 9);
    });

    test('剛好是現在這一刻算已過，排明天（不會立刻響）', () {
      final at = nextOccurrence(
          const RoutineSchedule(freq: 'daily', time: '10:30'), now());

      expect(at!.day, 27);
    });
  });

  group('每週', () {
    test('本週還沒到就排本週', () {
      // 現在是週日；排週日 19:00 → 今天稍晚
      final at = nextOccurrence(
          const RoutineSchedule(freq: 'weekly', weekday: 7, time: '19:00'),
          now());

      expect(at!.day, 26);
      expect(at.weekday, DateTime.sunday);
    });

    test('本週已過就排下週同一天', () {
      // 週日 09:00 已經過了 → 下週日
      final at = nextOccurrence(
          const RoutineSchedule(freq: 'weekly', weekday: 7, time: '09:00'),
          now());

      expect(at!.day, 8 - 6); // 8/2
      expect(at.month, 8);
      expect(at.weekday, DateTime.sunday);
    });

    test('排在本週稍後的星期幾', () {
      // 現在週日，排週三 16:00 → 三天後（7/29）
      final at = nextOccurrence(
          const RoutineSchedule(freq: 'weekly', weekday: 3, time: '16:00'),
          now());

      expect(at!.day, 29);
      expect(at.weekday, DateTime.wednesday);
      expect(at.hour, 16);
    });

    test('weekday 超出 1–7 不排', () {
      expect(
          nextOccurrence(
              const RoutineSchedule(freq: 'weekly', weekday: 0, time: '09:00'),
              now()),
          isNull);
      expect(
          nextOccurrence(
              const RoutineSchedule(freq: 'weekly', weekday: 8, time: '09:00'),
              now()),
          isNull);
    });
  });

  group('單次', () {
    test('未來的日期照排', () {
      final at = nextOccurrence(
          const RoutineSchedule(
              freq: 'once', date: '2026-07-29', time: '15:00'),
          now());

      expect(at!.month, 7);
      expect(at.day, 29);
      expect(at.hour, 15);
    });

    test('今天稍晚照排', () {
      final at = nextOccurrence(
          const RoutineSchedule(
              freq: 'once', date: '2026-07-26', time: '15:00'),
          now());

      expect(at!.day, 26);
    });

    test('已經過去的不排——過期的回診不該再響', () {
      expect(
        nextOccurrence(
            const RoutineSchedule(
                freq: 'once', date: '2026-07-20', time: '15:00'),
            now()),
        isNull,
      );
      expect(
        nextOccurrence(
            const RoutineSchedule(
                freq: 'once', date: '2026-07-26', time: '09:00'),
            now()),
        isNull,
      );
    });
  });

  group('壞資料不排也不炸', () {
    test('時間格式不對', () {
      for (final t in ['', '9', '25:00', '09:60', 'abc', '9:00:00']) {
        expect(nextOccurrence(RoutineSchedule(freq: 'daily', time: t), now()),
            isNull,
            reason: 'time=$t');
      }
    });

    test('沒有時間', () {
      expect(
          nextOccurrence(const RoutineSchedule(freq: 'daily'), now()), isNull);
    });

    test('沒見過的 freq', () {
      expect(
          nextOccurrence(
              const RoutineSchedule(freq: 'hourly', time: '09:00'), now()),
          isNull);
    });

    test('once 缺日期或日期格式不對', () {
      expect(
          nextOccurrence(
              const RoutineSchedule(freq: 'once', time: '15:00'), now()),
          isNull);
      expect(
          nextOccurrence(
              const RoutineSchedule(
                  freq: 'once', date: '2026/07/29', time: '15:00'),
              now()),
          isNull);
    });
  });

  group('通知 ID', () {
    test('同一個 routine 每次都得到同一個 ID（重排會覆蓋自己，不會疊兩則）', () {
      expect(notificationId('rtn_001'), notificationId('rtn_001'));
    });

    test('不同 routine 不撞號', () {
      final ids = [
        'rtn_001',
        'rtn_002',
        'rtn_003',
        'rtn_a1b2c3',
        'rtn_9f8e7d',
      ].map(notificationId).toSet();

      expect(ids.length, 5);
    });

    test('一律是非負數（Android 通知 ID 不吃負值）', () {
      for (final id in ['rtn_001', 'rtn_zzz', 'x', '']) {
        expect(notificationId(id), greaterThanOrEqualTo(0));
      }
    });
  });
}
