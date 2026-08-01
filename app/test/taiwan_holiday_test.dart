import 'package:e_hakka_care/shared/services/taiwan_holiday.dart';
import 'package:flutter_test/flutter_test.dart';

/// 假日判斷決定農民曆牌面的顏色（假日朱紅、平日藍），錯了長輩一眼就看得出來，
/// 但那是「顏色怪怪的」而不是「當掉」，不測就不會被發現，所以逐條釘住。
///
/// 期望值取自行政院人事行政總處公告的辦公日曆表（開放資料），不是自己推的。
void main() {
  group('2026 官方辦公日曆表', () {
    test('固定日期的國定假日', () {
      expect(taiwanHolidayOf(DateTime(2026, 1, 1))?.name, '開國紀念日');
      expect(taiwanHolidayOf(DateTime(2026, 2, 28))?.name, '和平紀念日');
      expect(taiwanHolidayOf(DateTime(2026, 4, 4))?.name, '兒童節');
      expect(taiwanHolidayOf(DateTime(2026, 5, 1))?.name, '勞動節');
      expect(taiwanHolidayOf(DateTime(2026, 10, 10))?.name, '國慶日');
      expect(taiwanHolidayOf(DateTime(2026, 12, 25))?.name, '行憲紀念日');
    });

    test('2026 年起新增放假的教師節與光復節', () {
      expect(taiwanHolidayOf(DateTime(2026, 9, 28))?.name, '孔子誕辰紀念日/教師節');
      expect(taiwanHolidayOf(DateTime(2026, 10, 25))?.name, '臺灣光復暨金門古寧頭大捷紀念日');
    });

    test('農曆假日與小年夜', () {
      expect(taiwanHolidayOf(DateTime(2026, 2, 15))?.name, '小年夜');
      expect(taiwanHolidayOf(DateTime(2026, 2, 16))?.name, '農曆除夕');
      expect(taiwanHolidayOf(DateTime(2026, 2, 17))?.name, '春節');
      expect(taiwanHolidayOf(DateTime(2026, 2, 19))?.name, '春節');
      expect(taiwanHolidayOf(DateTime(2026, 4, 5))?.name, '清明節');
      expect(taiwanHolidayOf(DateTime(2026, 6, 19))?.name, '端午節');
      expect(taiwanHolidayOf(DateTime(2026, 9, 25))?.name, '中秋節');
    });

    test('補假：連假之後順延的那一天也放假', () {
      // 這是純計算版漏掉的案例——小年夜 2/15 落在週日，但 2/16–2/19 連著都是假日，
      // 補假因此推到整個連假之後的 2/20，不是「往前看一天」推得出來的。
      final makeUp = taiwanHolidayOf(DateTime(2026, 2, 20));
      expect(makeUp?.name, '補假');
      expect(makeUp?.isMakeUp, isTrue);
    });

    test('其餘補假日', () {
      for (final d in [
        DateTime(2026, 2, 27),
        DateTime(2026, 4, 3),
        DateTime(2026, 4, 6),
        DateTime(2026, 10, 9),
        DateTime(2026, 10, 26),
      ]) {
        expect(taiwanHolidayOf(d)?.isMakeUp, isTrue, reason: '$d 應為補假');
      }
    });

    test('節日當天不標記為補假', () {
      expect(taiwanHolidayOf(DateTime(2026, 2, 28))?.isMakeUp, isFalse);
    });
  });

  group('2027 官方辦公日曆表', () {
    test('小年夜落在平日也認得（2027/2/4 是週四）', () {
      expect(DateTime(2027, 2, 4).weekday, DateTime.thursday);
      expect(taiwanHolidayOf(DateTime(2027, 2, 4))?.name, '小年夜');
    });

    test('連續兩天補假', () {
      expect(taiwanHolidayOf(DateTime(2027, 2, 9))?.isMakeUp, isTrue);
      expect(taiwanHolidayOf(DateTime(2027, 2, 10))?.isMakeUp, isTrue);
    });

    test('跨年補假（12/31 為次年元旦的補假）', () {
      expect(taiwanHolidayOf(DateTime(2027, 12, 31))?.isMakeUp, isTrue);
    });
  });

  group('台灣不放假的農曆節日不可判成假日', () {
    // 重構前的 bug：舊碼用 lunar 套件的 getFestivals() 判斷，那是中國節日表，
    // 元宵、重陽都在裡面，於是上班日被畫成紅的。
    test('元宵節（農曆正月十五）是平日', () {
      final d = DateTime(2026, 3, 3);
      expect(d.weekday, DateTime.tuesday, reason: '前提：這天不是週末');
      expect(taiwanHolidayOf(d), isNull, reason: '元宵在台灣不放假');
    });

    test('重陽節（農曆九月初九）是平日', () {
      final d = DateTime(2026, 10, 18);
      if (d.weekday != DateTime.saturday && d.weekday != DateTime.sunday) {
        expect(taiwanHolidayOf(d), isNull, reason: '重陽在台灣不放假');
      }
    });
  });

  group('週末與平日', () {
    test('週末算放假', () {
      expect(isTaiwanHoliday(DateTime(2026, 7, 25)), isTrue); // 週六
      expect(isTaiwanHoliday(DateTime(2026, 7, 26)), isTrue); // 週日
    });

    test('一般上班日不放假', () {
      expect(taiwanHolidayOf(DateTime(2026, 7, 30)), isNull); // 週四
      expect(isTaiwanHoliday(DateTime(2026, 7, 30)), isFalse);
    });
  });

  group('沒有官方資料的年份走推導，不整年判成平日', () {
    // 2030 不在表內。推導版已知漏補假（見實作檔頭），但固定假日與農曆假日仍要對。
    test('固定假日仍判得出來', () {
      expect(taiwanHolidayOf(DateTime(2030, 10, 10))?.name, '國慶日');
      expect(taiwanHolidayOf(DateTime(2030, 1, 1))?.name, '開國紀念日');
    });

    test('農曆假日仍判得出來', () {
      expect(isTaiwanHoliday(DateTime(2030, 2, 3)), isTrue); // 2030 春節初一
    });
  });
}
