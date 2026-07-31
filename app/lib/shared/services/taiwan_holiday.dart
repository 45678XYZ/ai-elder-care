/// 台灣的放假日判斷——決定農民曆牌面用朱紅（假日）還是藍（平日）。
///
/// 為什麼要獨立一支：原本 `today_screen` 與 `calendar_tear` 各寫一份「週末或農曆節日
/// 就是假日」，除了重複，判斷本身也不對——`lunar` 套件的 `getFestivals()` 是中國節日表，
/// 元宵、重陽、臘八都在裡面，但**台灣這三個不放假**，照那份表塗色會把上班日畫成紅的。
/// 節日「顯示」與「放不放假」是兩件事：牌面照樣要寫元宵（對長輩有意義），只是不塗紅。
///
/// ## 為什麼是查表而不是算出來
///
/// 假日**不能用曆法推導**，這是實測出來的結論。原本寫過一版純計算的（固定日期 + 節氣
/// 清明 + 農曆節日 + 「逢週六補週五、逢週日補週一」），拿 2026、2027 的官方資料一比就
/// 露餡：
///
/// - **小年夜**（除夕前一日）是法定假日，但它在 2027 落在週四，純計算完全漏掉。
/// - **補假會累積順延**。2026 小年夜 2/15 是週日，但 2/16–2/19 連著都是假日，補假因此
///   推到整個連假之後的 2/20（週五）。「往前看一天」的規則只在單一假日時成立，連假越長
///   錯得越多；2027 春節甚至連補兩天（2/9、2/10）。
///
/// 所以改為內建官方資料。唯一權威來源是行政院人事行政總處公告的「政府行政機關辦公
/// 日曆表」，本檔的表取自其開放資料（政府資料開放平臺 dataset 14718，社群鏡像
/// github.com/ruyut/TaiwanCalendar 提供逐年 JSON）。
///
/// 人事總處每年 6 月 30 日前公告次年度日曆表（特殊情形可延至 8 月 31 日）。
/// TODO: 2028 年度公告後把新的一年補進 [_holidays]；表以外的年份會落到
///   [_approximate] 的推導邏輯，那條路徑已知會漏小年夜與連假補假。
///
/// 註：辦公日曆表適用於政府機關，民間企業依勞基法與勞資協商，實務上多比照但不完全等同。
/// 本 App 是給長輩看日曆用的，比照政府行事曆即可。
library;

import 'package:lunar/lunar.dart';

/// 一個放假日。
class TaiwanHoliday {
  const TaiwanHoliday(this.name, {this.isMakeUp = false});

  /// 假日名稱，如「國慶日」。名稱沿用官方用字，未自行簡化。
  final String name;

  /// 是否為補假（本日並非節日當天，而是因節日與例假日重疊而順延）。
  final bool isMakeUp;
}

const _makeUpName = '補假';

/// 官方辦公日曆表中「有名稱的放假日」：年 → (`月日` 四碼 → 名稱)。
///
/// 只收有名稱的條目。純週末不列——那用星期幾就判得出來，列進來只是讓表變十倍長。
///
/// 2026 年起依《紀念日及節日實施條例》**不再有彈性放假與補班日**，所以不需要處理
/// 「週六卻要上班」的反向情況；若未來恢復，這裡要多一張補班表。
const _holidays = <int, Map<String, String>>{
  2026: {
    '0101': '開國紀念日',
    '0215': '小年夜',
    '0216': '農曆除夕',
    '0217': '春節',
    '0218': '春節',
    '0219': '春節',
    '0220': _makeUpName,
    '0227': _makeUpName,
    '0228': '和平紀念日',
    '0403': _makeUpName,
    '0404': '兒童節',
    '0405': '清明節',
    '0406': _makeUpName,
    '0501': '勞動節',
    '0619': '端午節',
    '0925': '中秋節',
    '0928': '孔子誕辰紀念日/教師節',
    '1009': _makeUpName,
    '1010': '國慶日',
    '1025': '臺灣光復暨金門古寧頭大捷紀念日',
    '1026': _makeUpName,
    '1225': '行憲紀念日',
  },
  2027: {
    '0101': '開國紀念日',
    '0204': '小年夜',
    '0205': '農曆除夕',
    '0206': '春節',
    '0207': '春節',
    '0208': '春節',
    '0209': _makeUpName,
    '0210': _makeUpName,
    '0228': '和平紀念日',
    '0301': _makeUpName,
    '0404': '兒童節',
    '0405': '清明節',
    '0406': _makeUpName,
    '0430': _makeUpName,
    '0501': '勞動節',
    '0609': '端午節',
    '0915': '中秋節',
    '0928': '孔子誕辰紀念日/教師節',
    '1010': '國慶日',
    '1011': _makeUpName,
    '1025': '臺灣光復暨金門古寧頭大捷紀念日',
    '1224': _makeUpName,
    '1225': '行憲紀念日',
    '1231': _makeUpName,
  },
};

/// 這一天放不放假。週末、國定假日、補假都算。
bool isTaiwanHoliday(DateTime date) => taiwanHolidayOf(date) != null;

/// 這一天是什麼假日；平日回 null。
TaiwanHoliday? taiwanHolidayOf(DateTime date) {
  final isWeekend =
      date.weekday == DateTime.saturday || date.weekday == DateTime.sunday;

  final table = _holidays[date.year];
  if (table != null) {
    final name = table[_monthDayKey(date)];
    if (name != null) {
      return TaiwanHoliday(name, isMakeUp: name == _makeUpName);
    }
    // 有這一年的官方資料，表裡沒有就是真的沒有；週末仍然放假。
    return isWeekend ? const TaiwanHoliday('例假日') : null;
  }

  // 沒有該年度的官方資料時的退路。已知不準（見檔頭），但總比整年都判成平日好。
  final approximate = _approximate(date);
  if (approximate != null) return TaiwanHoliday(approximate);
  return isWeekend ? const TaiwanHoliday('例假日') : null;
}

String _monthDayKey(DateTime d) =>
    '${d.month.toString().padLeft(2, '0')}${d.day.toString().padLeft(2, '0')}';

/// 沒有官方資料那幾年的推導版本：固定日期 + 節氣清明 + 農曆假日。
///
/// **刻意不推補假**——補假會隨連假長度累積順延（見檔頭），用簡化規則算出來的日期是錯的，
/// 錯的紅色比少一個紅色更糟：長輩會照著它安排回診。少標的那天至少只是「看起來像上班日」。
String? _approximate(DateTime date) {
  const fixed = <int, String>{
    101: '開國紀念日',
    228: '和平紀念日',
    404: '兒童節',
    501: '勞動節',
    928: '孔子誕辰紀念日/教師節',
    1010: '國慶日',
    1025: '臺灣光復暨金門古寧頭大捷紀念日',
    1225: '行憲紀念日',
  };
  final named = fixed[date.month * 100 + date.day];
  if (named != null) return named;

  final lunar = Lunar.fromDate(date);

  // 清明走節氣而非固定日期：它是太陽黃經 15° 的那一天，每年在 4/4 或 4/5 之間跳動。
  // 套件輸出簡體「清明」，與繁體同形，可直接比對。
  if (lunar.getJieQi() == '清明') return '清明節';

  // 閏月在此套件為負數（閏六月為 -6），所以正數比對天然排除閏月，
  // 不會把閏五月初五也當成端午。
  final month = lunar.getMonth();
  final day = lunar.getDay();
  if (month == 1 && day >= 1 && day <= 3) return '春節';
  if (month == 5 && day == 5) return '端午節';
  if (month == 8 && day == 15) return '中秋節';

  // 除夕與小年夜是農曆年的最後兩天，日數不固定（大月三十、小月廿九），
  // 所以用「幾天後是正月初一」反推，比查月份天數可靠。
  if (_isLunarNewYearDay(date.add(const Duration(days: 1)))) return '農曆除夕';
  if (_isLunarNewYearDay(date.add(const Duration(days: 2)))) return '小年夜';

  return null;
}

bool _isLunarNewYearDay(DateTime date) {
  final l = Lunar.fromDate(date);
  return l.getMonth() == 1 && l.getDay() == 1;
}
