import 'package:e_hakka_care/shared/services/calendar_tear_store.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:shared_preferences/shared_preferences.dart';

/// 「每天只播一次」的判斷。
///
/// 這是整個撕曆功能唯一有狀態的部分，也是最容易出錯的地方：播太多次很煩，
/// 播不出來等於功能不存在，而兩者都很難靠手動測試抓到（要等隔天）。
void main() {
  setUp(() => SharedPreferences.setMockInitialValues({}));

  final store = CalendarTearStore.instance;
  final today = DateTime(2026, 7, 28, 9);

  test('第一次安裝會播', () async {
    expect(await store.shouldPlayAndMark(today), isTrue);
  });

  test('同一天第二次不播', () async {
    await store.shouldPlayAndMark(today);
    expect(await store.shouldPlayAndMark(today), isFalse);
  });

  test('同一天連續叫很多次也只播第一次', () async {
    final results = <bool>[];
    for (var i = 0; i < 10; i++) {
      results.add(await store.shouldPlayAndMark(today));
    }
    expect(results.where((played) => played).length, 1);
  });

  test('隔天會再播', () async {
    await store.shouldPlayAndMark(today);
    expect(await store.shouldPlayAndMark(DateTime(2026, 7, 29, 8)), isTrue);
  });

  test('跨月跨年也算隔天', () async {
    await store.shouldPlayAndMark(DateTime(2026, 12, 31, 23));
    expect(await store.shouldPlayAndMark(DateTime(2027, 1, 1, 0, 5)), isTrue);
  });

  test('同一天不同時間點不會重播', () async {
    await store.shouldPlayAndMark(DateTime(2026, 7, 28, 0, 1));
    expect(
        await store.shouldPlayAndMark(DateTime(2026, 7, 28, 23, 59)), isFalse);
  });

  group('裝置時鐘被調動', () {
    test('往前調一天：當成新的一天，會播', () async {
      await store.shouldPlayAndMark(today);
      expect(await store.shouldPlayAndMark(DateTime(2026, 7, 29)), isTrue);
    });

    test('往回調一天：不播', () async {
      await store.shouldPlayAndMark(today);
      expect(await store.shouldPlayAndMark(DateTime(2026, 7, 27)), isFalse);
    });

    test('往回調之後不會卡住——記錄拉回當下，真正的隔天照播', () async {
      await store.shouldPlayAndMark(DateTime(2026, 8, 20)); // 時鐘曾被調快
      await store.shouldPlayAndMark(today); // 調回來，不播但要修正紀錄
      // 若沒修正紀錄，這裡會一路不播到 8/20
      expect(await store.shouldPlayAndMark(DateTime(2026, 7, 29)), isTrue);
    });
  });

  test('日期字串可直接字典序比較', () {
    expect(CalendarTearStore.dateKey(DateTime(2026, 7, 8)), '2026-07-08');
    expect(
        CalendarTearStore.dateKey(DateTime(2026, 7, 8)).compareTo('2026-07-09'),
        lessThan(0));
    expect(
        CalendarTearStore.dateKey(DateTime(2026, 12, 1))
            .compareTo('2026-07-09'),
        greaterThan(0));
  });

  test('reset 之後會再播一次', () async {
    await store.shouldPlayAndMark(today);
    await store.reset();
    expect(await store.shouldPlayAndMark(today), isTrue);
  });
}
