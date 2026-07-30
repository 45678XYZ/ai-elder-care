import 'package:ai_elder_care/shared/services/taiwan_holiday.dart';
import 'package:flutter_test/flutter_test.dart';

/// 假日判斷決定農民曆牌面的顏色（假日朱紅、平日藍），錯了長輩一眼就看得出來，
/// 但那是「顏色怪怪的」而不是「當掉」，不測就不會被發現，所以逐條釘住。
void main() {
  group('固定日期的國定假日', () {
    test('元旦、和平紀念日、兒童節、勞動節、國慶日都放假', () {
      expect(taiwanHolidayOf(DateTime(2026, 1, 1))?.name, '開國紀念日');
      expect(taiwanHolidayOf(DateTime(2026, 2, 28))?.name, '和平紀念日');
      expect(taiwanHolidayOf(DateTime(2026, 4, 4))?.name, '兒童節');
      expect(taiwanHolidayOf(DateTime(2026, 5, 1))?.name, '勞動節');
      expect(taiwanHolidayOf(DateTime(2026, 10, 10))?.name, '國慶日');
    });

    test('條例新增的教師節、光復節、行憲紀念日也放假', () {
      expect(taiwanHolidayOf(DateTime(2026, 9, 28))?.name, '教師節');
      expect(taiwanHolidayOf(DateTime(2026, 10, 25))?.name, '臺灣光復節');
      expect(taiwanHolidayOf(DateTime(2026, 12, 25))?.name, '行憲紀念日');
    });
  });

  group('農曆假日', () {
    test('2026 春節初一到初三', () {
      // 2026 農曆正月初一 = 2/17
      expect(taiwanHolidayOf(DateTime(2026, 2, 17))?.name, '春節');
      expect(taiwanHolidayOf(DateTime(2026, 2, 18))?.name, '春節');
      expect(taiwanHolidayOf(DateTime(2026, 2, 19))?.name, '春節');
    });

    test('除夕靠「明天是初一」反推，不寫死日數', () {
      expect(taiwanHolidayOf(DateTime(2026, 2, 16))?.name, '除夕');
    });

    test('端午與中秋', () {
      expect(taiwanHolidayOf(DateTime(2026, 6, 19))?.name, '端午節');
      expect(taiwanHolidayOf(DateTime(2026, 9, 25))?.name, '中秋節');
    });

    test('清明走節氣，不是寫死 4/5', () {
      // 2026 清明在 4/5；當天同時不是 4/4，證明不是靠固定日期矇到的
      expect(taiwanHolidayOf(DateTime(2026, 4, 5))?.name, '清明節');
    });
  });

  group('台灣不放假的農曆節日不可判成假日', () {
    // 這是重構前的 bug：舊碼用 lunar 套件的 getFestivals() 判斷，那是中國節日表，
    // 元宵、重陽都在裡面，於是上班日被畫成紅的。
    test('元宵節（農曆正月十五）是平日', () {
      final d = DateTime(2026, 3, 3); // 2026 元宵
      if (d.weekday != DateTime.saturday && d.weekday != DateTime.sunday) {
        expect(taiwanHolidayOf(d), isNull, reason: '元宵在台灣不放假');
      }
    });

    test('重陽節（農曆九月初九）是平日', () {
      final d = DateTime(2026, 10, 18); // 2026 重陽
      if (d.weekday != DateTime.saturday && d.weekday != DateTime.sunday) {
        expect(taiwanHolidayOf(d), isNull, reason: '重陽在台灣不放假');
      }
    });
  });

  group('補假', () {
    test('假日逢週六，補前一個上班日（週五）', () {
      // 2026/2/28 和平紀念日是星期六
      expect(DateTime(2026, 2, 28).weekday, DateTime.saturday);
      final friday = taiwanHolidayOf(DateTime(2026, 2, 27));
      expect(friday?.name, '和平紀念日');
      expect(friday?.isMakeUp, isTrue);
    });

    test('補假標記與節日當天分得開', () {
      expect(taiwanHolidayOf(DateTime(2026, 2, 28))?.isMakeUp, isFalse);
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
}
