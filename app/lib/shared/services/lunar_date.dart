import 'package:lunar/lunar.dart';

/// 農曆日期（給長者模式的農民曆牌面用）。
///
/// 包一層 `lunar` 套件的原因有兩個：
/// 1. 它的輸出是**簡體**（生肖「马」、節日「春节」、節氣「惊蛰」），本專案一律繁體，
///    所以在這裡轉完再交給畫面，畫面不必知道有這回事。
/// 2. 把第三方套件關在單一檔案裡，之後要換套件或改自繪只動這裡。
class LunarDate {
  const LunarDate({
    required this.monthDay,
    required this.ganZhiYear,
    required this.zodiac,
    this.jieQi,
    this.festival,
  });

  /// 農曆月日，如「六月十三」。閏月為「閏六月十三」。
  final String monthDay;

  /// 干支年，如「丙午」。
  final String ganZhiYear;

  /// 生肖，如「馬」。
  final String zodiac;

  /// 當天是節氣才有，如「立秋」；否則 null。
  final String? jieQi;

  /// 當天是農曆節日才有，如「中秋節」；否則 null。
  final String? festival;

  /// 牌面上要強調的那一則：節日優先於節氣（節日對長輩更有感）。
  String? get highlight => festival ?? jieQi;

  factory LunarDate.of(DateTime date) {
    final l = Lunar.fromDate(date);
    final jieQi = _clean(l.getJieQi());
    final festivals = l.getFestivals();

    return LunarDate(
      monthDay:
          _toTraditional('${l.getMonthInChinese()}月${l.getDayInChinese()}'),
      ganZhiYear: _toTraditional(l.getYearInGanZhi()),
      zodiac: _toTraditional(l.getYearShengXiao()),
      jieQi: jieQi == null ? null : _toTraditional(jieQi),
      festival:
          festivals.isEmpty ? null : _toTraditional(festivals.first.toString()),
    );
  }

  static String? _clean(String? v) =>
      (v == null || v.trim().isEmpty) ? null : v.trim();

  /// 簡→繁。只涵蓋農曆語境會出現的字（節氣、生肖、節日、閏月），
  /// 不是通用轉換器——別拿去轉別的文字。
  static String _toTraditional(String s) {
    var out = s;
    _charMap.forEach((from, to) => out = out.replaceAll(from, to));
    return out;
  }

  static const _charMap = <String, String>{
    // 閏月
    '闰': '閏',
    // 生肖
    '龙': '龍',
    '马': '馬',
    '鸡': '雞',
    '猪': '豬',
    // 節氣
    '惊': '驚',
    '蛰': '蟄',
    '谷': '穀',
    '满': '滿',
    '种': '種',
    '处': '處',
    // 節日
    '节': '節',
    '阳': '陽',
    '腊': '臘',
    '农': '農',
    '妈': '媽',
    '诞': '誕',
    '观': '觀',
    '关': '關',
    '华': '華',
    '严': '嚴',
  };
}
