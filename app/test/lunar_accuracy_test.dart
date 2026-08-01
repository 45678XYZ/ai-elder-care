import 'package:e_hakka_care/shared/services/lunar_date.dart';
import 'package:flutter_test/flutter_test.dart';

/// 農曆換算的正確性驗收。
///
/// `lunar` 套件是第三方（中國開發者的 lunar-java 移植），不能只相信它「有回東西」。
/// 這裡拿**可獨立查證的節日與節氣**對照國曆日期——這些日子在任何萬年曆上都查得到，
/// 對不上就代表套件或版本有問題，測試會直接擋下來。
void main() {
  group('2026 年農曆節日對應國曆', () {
    // 每一筆都可用政府行事曆或任何萬年曆核對
    final cases = <String, (DateTime, String)>{
      '春節（正月初一）': (DateTime(2026, 2, 17), '正月初一'),
      '元宵（正月十五）': (DateTime(2026, 3, 3), '正月十五'),
      '端午（五月初五）': (DateTime(2026, 6, 19), '五月初五'),
      '中秋（八月十五）': (DateTime(2026, 9, 25), '八月十五'),
    };

    cases.forEach((name, expected) {
      test(name, () {
        expect(LunarDate.of(expected.$1).monthDay, expected.$2);
      });
    });
  });

  group('節氣落在正確的國曆日', () {
    // 節氣由天文計算決定，每年日期固定在一兩天內
    final cases = <DateTime, String>{
      DateTime(2026, 2, 4): '立春',
      DateTime(2026, 3, 20): '春分',
      DateTime(2026, 6, 21): '夏至',
      DateTime(2026, 8, 7): '立秋',
      // 2026 冬至的天文時刻是 UTC 12/21 晚間，換到 +08:00 落在 12/22——
      // 套件算的是東八區，對台灣正確。2025 年則是 12/21，可見它不是寫死日期。
      DateTime(2026, 12, 22): '冬至',
      DateTime(2025, 12, 21): '冬至',
    };

    cases.forEach((date, name) {
      test('${date.month}/${date.day} 是$name', () {
        expect(LunarDate.of(date).jieQi, name);
      });
    });
  });

  group('干支與生肖', () {
    test('2026 是丙午馬年', () {
      final d = LunarDate.of(DateTime(2026, 7, 26));
      expect(d.ganZhiYear, '丙午');
      expect(d.zodiac, '馬');
    });

    test('農曆年以春節為界，不是元旦', () {
      expect(LunarDate.of(DateTime(2026, 2, 16)).ganZhiYear, '乙巳'); // 春節前一天
      expect(LunarDate.of(DateTime(2026, 2, 17)).ganZhiYear, '丙午'); // 春節當天
    });
  });
}
