import 'package:shared_preferences/shared_preferences.dart';

/// 「今天播過撕曆動畫了沒」的狀態。
///
/// 只存本機、不同步雲端：這是視覺裝飾，跨裝置一致性沒有價值，同步只會引入
/// 不必要的失敗模式。
///
/// 判斷用**裝置本地日期**（yyyy-MM-dd 字串，可直接字典序比較），不是 UTC——
/// 長輩在意的是「今天」，那是他手機上的今天。
class CalendarTearStore {
  CalendarTearStore._();
  static final CalendarTearStore instance = CalendarTearStore._();

  static const _key = 'calendar_tear_last_shown';

  /// 測試用的注入點；正式路徑走 SharedPreferences。
  static String dateKey(DateTime d) =>
      '${d.year}-${_two(d.month)}-${_two(d.day)}';

  static String _two(int v) => v.toString().padLeft(2, '0');

  /// 今天是否該播；該播的話**同時標記為已播**。
  ///
  /// 標記時機刻意在「開始播」而不是「播完」：動畫播到一半 App 被系統殺掉時，
  /// 下次開啟不該又從頭播一次。少看半次動畫，好過重複看。
  ///
  /// 裝置時鐘被往回調（stored > today）時不播，但把記錄拉回今天——否則使用者
  /// 之後每天都會看到動畫，直到真實日期追上那個被調快的紀錄。
  Future<bool> shouldPlayAndMark(DateTime now) async {
    final prefs = await SharedPreferences.getInstance();
    final today = dateKey(now);
    final stored = prefs.getString(_key);

    if (stored == today) return false;

    if (stored != null && stored.compareTo(today) > 0) {
      await prefs.setString(_key, today);
      return false;
    }

    await prefs.setString(_key, today);
    return true;
  }

  /// 測試與「重看一次」用。
  Future<void> reset() async {
    final prefs = await SharedPreferences.getInstance();
    await prefs.remove(_key);
  }
}
