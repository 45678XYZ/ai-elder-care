/// 台灣的放假日判斷——決定農民曆牌面用朱紅（假日）還是藍（平日）。
///
/// 為什麼要獨立一支：原本 `today_screen` 與 `calendar_tear` 各寫一份「週末或農曆節日
/// 就是假日」，除了重複，判斷本身也不對——`lunar` 套件的 `getFestivals()` 是中國節日表，
/// 元宵、重陽、臘八都在裡面，但**台灣這三個不放假**，照那份表塗色會把上班日畫成紅的。
/// 節日「顯示」與「放不放假」是兩件事：牌面照樣要寫元宵（對長輩有意義），只是不塗紅。
///
/// ## 算得出來的與算不出來的
///
/// 這裡只處理**規則明確、可由日期推導**的部分：
/// - 週末
/// - 固定日期的國定假日（元旦、和平紀念日、兒童節…）
/// - 清明（走節氣，每年落在 4/4 或 4/5）
/// - 農曆假日（除夕、春節初一到初三、端午、中秋）
/// - 例假日重疊時的補假（逢週六補前一個上班日、逢週日補次一個上班日）
///
/// **算不出來的是「彈性調整放假」**——人事行政總處每年公告的連假挪移（例如把某個
/// 星期五調成放假、隔週六補上班）。那是行政決定不是曆法，沒有公式可推，要正確就得
/// 每年放一份官方行事曆對照表進來。目前不做，後果是連假的「橋接日」會被畫成平日藍。
///
/// TODO: 若要精準到連假，改為讀入人事行政總處公告的當年度行事曆（政府資料開放平臺
///   有 CSV），本檔的推導邏輯退為沒有資料時的 fallback。
library;

import 'package:lunar/lunar.dart';

/// 一個放假日。
class TaiwanHoliday {
  const TaiwanHoliday(this.name, {this.isMakeUp = false});

  /// 假日名稱，如「國慶日」。補假時為原假日名稱。
  final String name;

  /// 是否為補假（本日並非節日當天，而是因節日落在例假日而順延）。
  final bool isMakeUp;
}

/// 國曆固定日期的放假日：`月 * 100 + 日` → 名稱。
///
/// 依《紀念日及節日實施條例》（2025 年制定，2026 年起施行）。該法把原本只紀念不放假的
/// 教師節、光復節、行憲紀念日改為放假日，這份表已納入。
///
/// TODO(verify): 這份清單依法條整理，未與人事行政總處當年度公告逐項核對過。上線前
///   請對一次官方行事曆——放假日少一天，長輩看到的顏色就是錯的。
const _fixedHolidays = <int, String>{
  101: '開國紀念日',
  228: '和平紀念日',
  404: '兒童節',
  501: '勞動節',
  928: '教師節',
  1010: '國慶日',
  1025: '臺灣光復節',
  1225: '行憲紀念日',
};

/// 這一天放不放假。週末、國定假日、農曆假日、補假都算。
bool isTaiwanHoliday(DateTime date) => taiwanHolidayOf(date) != null;

/// 這一天是什麼假日；平日回 null。
///
/// 週末回 null 之外的判斷順序：先看本日是不是節日本身，再看是不是別的節日的補假。
/// 週末本身也是放假，但沒有名稱可回，所以由 [isTaiwanHoliday] 另外處理。
TaiwanHoliday? taiwanHolidayOf(DateTime date) {
  final own = _statutoryHolidayOn(date);
  if (own != null) return TaiwanHoliday(own);

  final weekend =
      date.weekday == DateTime.saturday || date.weekday == DateTime.sunday;
  if (weekend) return const TaiwanHoliday('例假日');

  // 補假：週六的假日補前一個上班日（週五），週日的假日補次一個上班日（週一）。
  // 只往回／往前看一天就夠——連續兩天都是假日時，第二天自己就是假日，不需要補。
  if (date.weekday == DateTime.friday) {
    final saturday = date.add(const Duration(days: 1));
    final name = _statutoryHolidayOn(saturday);
    if (name != null) return TaiwanHoliday(name, isMakeUp: true);
  }
  if (date.weekday == DateTime.monday) {
    final sunday = date.subtract(const Duration(days: 1));
    final name = _statutoryHolidayOn(sunday);
    if (name != null) return TaiwanHoliday(name, isMakeUp: true);
  }

  return null;
}

/// 本日是否為節日**當天**（不含週末與補假）；是則回名稱。
String? _statutoryHolidayOn(DateTime date) {
  final fixed = _fixedHolidays[date.month * 100 + date.day];
  if (fixed != null) return fixed;

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

  // 除夕是農曆年的最後一天，日數不固定（大月三十、小月廿九），
  // 所以用「明天是不是正月初一」反推，比查月份天數可靠。
  final tomorrow = Lunar.fromDate(date.add(const Duration(days: 1)));
  if (tomorrow.getMonth() == 1 && tomorrow.getDay() == 1) return '除夕';

  return null;
}
