import 'dart:math' as math;

import 'package:flutter/material.dart';

import '../../shared/services/lunar_date.dart';
import '../../theme/app_theme.dart';

/// 農民曆牌面——照傳統撕曆的版面，**所有尺寸共用這一個**（首頁小卡、撕曆過場、
/// 放大檢視）。小卡與放大檢視只差在大小，版面一模一樣。
///
/// ```
/// 2026   歲次丙午年          7
///                           月
/// 六
/// 月
/// 十          2 8
/// 五
/// 日      星 期 二
/// ```
///
/// 四角各司其職：左上國曆年、中上歲次干支、**右上角**月份（數字大、「月」在下）、
/// 左緣農曆直排；中央整片留給大日期。
///
/// 版面規則只有三條，其餘都是它們的結果：
/// 1. 頂列貼上緣、星期貼下緣、農曆貼左緣——元素往四邊靠，不擠在中央
/// 2. 中段（農曆直排右緣→右邊界、頂列下緣→星期上緣）整片是大日期的，
///    數字撐滿這一片並置中。牌面有多大字就有多大
/// 3. 字級是一組互相依賴的比例（MASTER.md「農民曆牌面」那張表），整組乘上
///    `牌面寬度 / AlmanacTypography.refWidth`
///
/// 不跟隨系統字級縮放（[MediaQuery.withNoTextScaling]）：字級已經由牌面寬度決定，
/// 本來就大於任何放大後的一般內文，再乘一次只會爆版。要看更大就點開放大檢視。
class AlmanacFace extends StatelessWidget {
  const AlmanacFace({
    super.key,
    required this.date,
    required this.lunar,
    required this.color,
  });

  final DateTime date;
  final LunarDate lunar;

  /// 台灣日曆慣例：假日朱紅、平日藍。由呼叫端判斷後傳進來。
  final Color color;

  static const _weekdays = ['一', '二', '三', '四', '五', '六', '日'];

  @override
  Widget build(BuildContext context) {
    return MediaQuery.withNoTextScaling(
      child: LayoutBuilder(
        builder: (context, constraints) {
          final w = constraints.maxWidth;
          final s = w / AlmanacTypography.refWidth;

          // 牌面比參考比例更高（例如放大到整個直式螢幕）時，整組字跟著長：
          // 大日期的大小被寬度卡住，不一起放大的話中間就空一大塊。
          // 上限 1.6——再往上頂列會先被寬度擠回去，長高沒有意義。
          final vf = constraints.hasBoundedHeight
              ? (constraints.maxHeight / w).clamp(1.0, 1.6)
              : 1.0;

          // 頂列與星期是**橫的一行**，只能長到把板面寬度用掉八成為止，再長就是
          // 一行字頂著兩邊（也就是「擠」）。直排農曆沒有這個限制，長高就跟著長。
          final headerScale = s * math.min(vf, 1.25);
          final weekdayScale = s * math.min(vf, 1.45);
          final lunarScale = s * vf;

          // 首頁小卡是半版寬，等比縮會掉到 8sp——那不是「小一點」是讀不到。
          double sized(double base, double scale, double floor) =>
              math.max(base * scale, floor);

          final band = _Band(
            day: '${date.day}',
            // 直排帶「農曆」二字。撕曆原本只寫「五月廿六日」，但這張日曆是給
            // 長輩看的，左緣那一行要一眼認得出是農曆，不能靠讀者自己推。
            lunarChars: '農曆${lunar.monthDay}'.characters.toList(),
            lunarSize: sized(AlmanacTypography.lunar, lunarScale,
                AlmanacTypography.minLunar),
            lunarGap: AlmanacTypography.lunarGap * lunarScale,
            color: color,
          );

          return Column(
            mainAxisSize: constraints.hasBoundedHeight
                ? MainAxisSize.max
                : MainAxisSize.min,
            children: [
              _header(headerScale, sized),
              if (constraints.hasBoundedHeight)
                Expanded(child: band)
              else
                SizedBox(height: w * 0.62, child: band),
              _weekdayLine(weekdayScale, sized),
              if (lunar.highlight != null) ...[
                SizedBox(height: AppSpacing.sm * headerScale),
                _highlightPill(headerScale, sized),
              ],
            ],
          );
        },
      ),
    );
  }

  /// 頂列：左國曆年、中歲次干支、右上角月份。
  ///
  /// 月份用 [Column] 貼在右角，數字大、「月」在下——撕曆就是這樣寫的，
  /// 而且這樣月份才會**待在角落**，不會被中間的干支推得像擠在一起。
  Widget _header(double scale, double Function(double, double, double) sized) {
    final yearStyle = AlmanacTypography.serif(
            sized(AlmanacTypography.year, scale, AlmanacTypography.minHeader),
            FontWeight.w900,
            height: 1.1)
        .copyWith(color: color);
    final ganZhiStyle = AlmanacTypography.serif(
            sized(AlmanacTypography.ganZhi, scale, AlmanacTypography.minHeader),
            FontWeight.w900,
            height: 1.1)
        .copyWith(color: color);

    // 三格的寬度比 3:5:2——干支五個字最長，年份次之，月份只有一個數字。
    // 每格各自靠邊（年靠左、干支置中、月份靠右角），格子有剩就是三者之間的空隙。
    return Row(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Expanded(
          flex: 3,
          child: FittedBox(
            fit: BoxFit.scaleDown,
            alignment: Alignment.topLeft,
            child: Text('${date.year}', style: yearStyle),
          ),
        ),
        Expanded(
          flex: 5,
          child: FittedBox(
            fit: BoxFit.scaleDown,
            child: Text('歲次${lunar.ganZhiYear}年', style: ganZhiStyle),
          ),
        ),
        Expanded(
          flex: 2,
          child: FittedBox(
            fit: BoxFit.scaleDown,
            alignment: Alignment.topRight,
            child: Column(
              mainAxisSize: MainAxisSize.min,
              children: [
                Text('${date.month}',
                    style: AlmanacTypography.serif(
                            sized(AlmanacTypography.monthNumber, scale,
                                AlmanacTypography.minHeader * 1.6),
                            FontWeight.w900,
                            height: 0.95)
                        .copyWith(color: color)),
                Text('月',
                    style: AlmanacTypography.serif(
                            sized(AlmanacTypography.monthLabel, scale,
                                AlmanacTypography.minHeader),
                            FontWeight.w900,
                            height: 1)
                        .copyWith(color: color)),
              ],
            ),
          ),
        ),
      ],
    );
  }

  /// 星期。字距會在最後一個字右側也加上間距，補一個左邊距才視覺置中。
  Widget _weekdayLine(
      double scale, double Function(double, double, double) sized) {
    final spacing = AlmanacTypography.weekdaySpacing * scale;
    return Padding(
      padding: EdgeInsets.only(left: spacing),
      child: FittedBox(
        fit: BoxFit.scaleDown,
        child: Text(
          '星期${_weekdays[date.weekday - 1]}',
          style: AlmanacTypography.serif(
            sized(
                AlmanacTypography.weekday, scale, AlmanacTypography.minWeekday),
            FontWeight.w900,
            height: 1.1,
            letterSpacing: spacing,
          ).copyWith(color: color),
        ),
      ),
    );
  }

  /// 節氣／農曆節日膠囊。只有當天有才出現。
  Widget _highlightPill(
      double scale, double Function(double, double, double) sized) {
    return FittedBox(
      fit: BoxFit.scaleDown,
      child: Container(
        padding: EdgeInsets.symmetric(
            horizontal: AppSpacing.md * scale, vertical: AppSpacing.xs * scale),
        decoration: BoxDecoration(
          color: color,
          borderRadius: const BorderRadius.all(AppRadius.pill),
        ),
        child: Text(
          lunar.highlight!,
          style: AlmanacTypography.serif(
                  sized(AlmanacTypography.ganZhi, scale,
                      AlmanacTypography.minHeader),
                  FontWeight.w900,
                  height: 1.1)
              .copyWith(color: Colors.white),
        ),
      ),
    );
  }
}

/// 牌面中段：左緣農曆直排，其餘**整片**都是大日期的。
///
/// [CrossAxisAlignment.stretch] 是關鍵——沒有它，右邊那格只會包住數字本身的高度，
/// 大日期就縮在中段頂端（看起來像「靠上」），而不是填滿中段置中。
class _Band extends StatelessWidget {
  const _Band({
    required this.day,
    required this.lunarChars,
    required this.lunarSize,
    required this.lunarGap,
    required this.color,
  });

  final String day;
  final List<String> lunarChars;
  final double lunarSize;
  final double lunarGap;
  final Color color;

  @override
  Widget build(BuildContext context) {
    return LayoutBuilder(
      builder: (context, constraints) {
        final n = lunarChars.length;
        final h = constraints.maxHeight;

        // 直排字級先由寬度決定；高度不夠時**先壓字距再縮字**——農曆這一行本來
        // 就是貼著排的，字距讓出來的空間拿去把字放大，比整排縮小好讀得多。
        var size =
            math.min(lunarSize, math.max((h - lunarGap * (n - 1)) / n, 1.0));
        var gap = lunarGap;
        if (size < lunarSize) {
          const tightRatio = 0.08; // 壓到剩字級的 8%
          size = math.min(lunarSize, h / (n + tightRatio * (n - 1)));
          gap = size * tightRatio;
        } else if (n > 1) {
          // 中段比直排高時，多出來的高度變成字距，讓這一欄撐開到上下兩端；
          // 但字距最多到 0.6 個字高，再撐字會散得像斷掉，那時改成整欄置中。
          final natural = size * n + lunarGap * (n - 1);
          gap = math.min(lunarGap + (h - natural) / (n - 1), size * 0.6);
        }

        return Row(
          crossAxisAlignment: CrossAxisAlignment.stretch,
          children: [
            SizedBox(
              // 一個字寬再多給一點，字面寬的字（曆、農）才不會被切到。
              width: size * 1.12,
              child: Align(
                // 上面算的是名目字級，實際行高會因字體度量多零點幾 px，
                // 整欄疊起來就足以擠爆中段。scaleDown 收這個尾差。
                child: FittedBox(
                  fit: BoxFit.scaleDown,
                  child: _VerticalText(
                    lunarChars,
                    gap: math.max(gap, 0),
                    style: AlmanacTypography.serif(size, FontWeight.w900,
                            height: 1)
                        .copyWith(color: color),
                  ),
                ),
              ),
            ),
            Expanded(
              child: Padding(
                // 數字的墨會略微超出文字框，留一點餘裕才不會頂到上下左右。
                padding: EdgeInsets.symmetric(
                    vertical: h * 0.02,
                    horizontal: constraints.maxWidth * 0.02),
                child: Center(
                  child: FittedBox(
                    // contain：撐滿中段（放不下時一樣會縮），大日期永遠是牌面上最大的東西。
                    fit: BoxFit.contain,
                    child: Transform.translate(
                      // 文字框置中不等於**數字看起來**置中：框裡下緣留著給注音符號
                      // 的降部空間，數字自己並不用到，置中的結果就是視覺偏下、
                      // 貼著「星期」那一行。往上推一個字級的 8%，讓數字的墨
                      // 落在頂列與星期的正中間。位移在 FittedBox 裡面，會跟著縮放。
                      offset: const Offset(0,
                          -AlmanacTypography.day * AlmanacTypography.dayLift),
                      child: Text(
                        day,
                        style: AlmanacTypography.serif(
                                AlmanacTypography.day, FontWeight.w900,
                                height: AlmanacTypography.dayHeight)
                            .copyWith(color: color),
                      ),
                    ),
                  ),
                ),
              ),
            ),
          ],
        );
      },
    );
  }
}

/// 中文直排：逐字往下排。
///
/// 不用 RotatedBox——那會把字也轉倒。中文直排本來就是「字不轉、往下疊」。
/// [gap] 是字與字的垂直間隔（直排的「字距」）；`letterSpacing` 在直排無效，
/// 因為每個字各自是一個 Text。
class _VerticalText extends StatelessWidget {
  const _VerticalText(this.chars, {required this.style, this.gap = 0});

  final List<String> chars;
  final TextStyle style;
  final double gap;

  @override
  Widget build(BuildContext context) {
    return Column(
      mainAxisSize: MainAxisSize.min,
      children: [
        for (var i = 0; i < chars.length; i++) ...[
          if (i > 0) SizedBox(height: gap),
          Text(chars[i], style: style),
        ],
      ],
    );
  }
}
