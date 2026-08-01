import 'package:e_hakka_care/shared/services/lunar_date.dart';
import 'package:flutter_test/flutter_test.dart';

/// `lunar` 套件輸出的是簡體（生肖「马」、節日「春节」、節氣「惊蛰」），
/// 這組測試盯住 [LunarDate] 有把它們轉成繁體——漏一個字就會直接出現在長者畫面上。
void main() {
  group('農曆換算', () {
    test('一般日期給出月日與干支', () {
      final d = LunarDate.of(DateTime(2026, 7, 26));

      expect(d.monthDay, '六月十三');
      expect(d.ganZhiYear, '丙午');
      expect(d.zodiac, '馬'); // 套件回簡體「马」
      expect(d.jieQi, isNull);
      expect(d.festival, isNull);
      expect(d.highlight, isNull);
    });

    test('節氣當天帶出節氣', () {
      final d = LunarDate.of(DateTime(2026, 8, 7));
      expect(d.jieQi, '立秋');
      expect(d.highlight, '立秋');
    });

    test('節日優先於節氣顯示', () {
      final d = LunarDate.of(DateTime(2026, 2, 17)); // 春節
      expect(d.festival, '春節'); // 套件回簡體「春节」
      expect(d.highlight, '春節');
    });
  });

  group('簡轉繁', () {
    test('生肖十二年都是繁體', () {
      // 逐年取農曆年初之後的日期，確認沒有簡體殘留
      const simplified = ['龙', '马', '鸡', '猪'];
      for (var year = 2020; year < 2032; year++) {
        final z = LunarDate.of(DateTime(year, 6, 15)).zodiac;
        expect(simplified, isNot(contains(z)), reason: '$year 年生肖 $z 仍是簡體');
      }
    });

    test('節氣名不含簡體字', () {
      // 掃過一整年，撈出所有節氣名
      const simplified = ['惊', '蛰', '谷', '满', '种', '处'];
      final found = <String>{};
      for (var i = 0; i < 365; i++) {
        final j =
            LunarDate.of(DateTime(2026, 1, 1).add(Duration(days: i))).jieQi;
        if (j != null) found.add(j);
      }

      expect(found.length, greaterThan(20), reason: '一年應該掃得到 24 節氣');
      for (final name in found) {
        for (final s in simplified) {
          expect(name.contains(s), isFalse, reason: '節氣「$name」仍含簡體字「$s」');
        }
      }
    });
  });
}
