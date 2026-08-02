import 'package:e_hakka_care/shared/models/daily_summary.dart';
import 'package:e_hakka_care/shared/models/life_event.dart';
import 'package:e_hakka_care/shared/models/routine.dart';
import 'package:e_hakka_care/shared/models/stats.dart';
import 'package:flutter_test/flutter_test.dart';

/// 後端時間戳解析後必須是**本地時間**。
///
/// api.md 的時間戳一律帶 `+08:00`（後端 `format_ts`），而 Dart 的 `DateTime.parse`
/// 遇到 offset 會換算成 **UTC** 回傳（`isUtc == true`）。少了 `.toLocal()`，
/// 顯示端讀到的 `.hour`／`.day` 全是 UTC 值——時間軸上早上九點的事會標成半夜一點，
/// 日期分隔還會跨錯天。這是實機才發現的，所以釘成測試。
///
/// 兩個斷言缺一不可：
/// - `isUtc == false`：確定有轉本地，而不是原封不動的 UTC。
/// - 絕對時間點不變：擋掉「手動 ±8 小時」那條錯路。時間點是物理事實，
///   轉時區只換表示法，換算完仍必須指向同一瞬間。
///
/// 刻意不斷言 `.hour == 9`：那會綁死執行測試的機器在台北時區，CI 換地方就紅。
void main() {
  /// 台灣時間 2026-07-14 09:05 == UTC 01:05（api.md 的 `ts` 範例）。
  const taipeiTs = '2026-07-14T09:05:00.000+08:00';
  final sameInstant = DateTime.utc(2026, 7, 14, 1, 5);

  void expectLocalSameInstant(DateTime? actual, String field) {
    expect(actual, isNotNull, reason: '$field 應該解析得出來');
    expect(actual!.isUtc, isFalse,
        reason: '$field 少了 .toLocal()，顯示端會讀到 UTC 的時分');
    expect(actual.millisecondsSinceEpoch, sameInstant.millisecondsSinceEpoch,
        reason: '$field 的絕對時間點被改動了——轉時區不該讓它指向另一個瞬間');
  }

  test('LifeEvent.ts 轉本地且不移動時間點', () {
    final e = LifeEvent.fromJson(const {
      'event_id': 'evt_1',
      'elder_id': 'eld_1',
      'ts': taipeiTs,
      'type': 'medication',
      'detail': '已服用血壓藥。',
      'source': 'conversation',
    });
    expectLocalSameInstant(e.ts, 'LifeEvent.ts');
  });

  test('RoutineOccurrence 的排程與完成時間轉本地', () {
    final o = RoutineOccurrence.fromJson(const {
      'routine_id': 'rtn_1',
      'title': '吃血壓藥',
      'type': 'medication',
      'scheduled_at': taipeiTs,
      'status': 'done',
      'completed_at': taipeiTs,
    });
    expectLocalSameInstant(o.scheduledAt, 'RoutineOccurrence.scheduledAt');
    expectLocalSameInstant(o.completedAt, 'RoutineOccurrence.completedAt');
  });

  test('DailySummary.generatedAt 轉本地', () {
    final s = DailySummary.fromJson(const {
      'elder_id': 'eld_1',
      'date': '2026-07-14',
      'generated_at': taipeiTs,
    });
    expectLocalSameInstant(s.generatedAt, 'DailySummary.generatedAt');
  });

  test('StatsToday.lastInteractionAt 轉本地', () {
    final s = StatsToday.fromJson(const {
      'interaction_count': 3,
      'last_interaction_at': taipeiTs,
    });
    expectLocalSameInstant(s.lastInteractionAt, 'StatsToday.lastInteractionAt');
  });

  test('沒有 ts 時退回 epoch，不會丟例外', () {
    final e = LifeEvent.fromJson(const {'event_id': 'evt_2'});
    expect(e.ts.millisecondsSinceEpoch, 0);
  });
}
