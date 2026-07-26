// 暖紙手帳 — 單一真實來源的 Theme 實作
// 對映 SKILL.md §2 顏色 / §3 字體 / §4 間距圓角陰影
// 依賴：google_fonts

import 'package:flutter/material.dart';
import 'package:google_fonts/google_fonts.dart';

/// §2 顏色。所有文字色對比以最深紙底 #e4dccb 為基準驗收。
abstract final class AppColors {
  // §2.1 紙感底色
  static const body = Color(0xFFE4DCCB);
  static const app = Color(0xFFF3ECDD);
  static const card = Color(0xFFFBF7EC);
  static const cardAlt = Color(0xFFFFFDF8);
  static const nest = Color(0xFFFAF6EE);
  static const chipSurface = Color(0xFFEFE8DB);
  static const track = Color(0xFFF2ECE1);
  static const barDark = Color(0xFF33291F);

  // §2.2 文字 — 只有兩階
  static const ink = Color(0xFF33291F);
  static const inkSecondary = Color(0xFF504333);
  static const onDark = Color(0xFFF5EAD9);
  static const onDarkSecondary = Color(0xFFC9B8A3);

  // §2.3 非文字專用（禁止當文字色）
  static const border = Color(0xFFE5DCCB);
  static const borderDashed = Color(0xFFD9C9B2);
  static const chevron = Color(0xFFC9B8A3);
  static const divider = Color(0xFFEFE8DB);

  // §2.4 朱紅 accent
  static const accentText = Color(0xFFAF3723); // 供 >=24sp 文字、白字實心底
  static const accent = Color(0xFFD15640); // 非文字：外框、外環、進度
  static const accentPressed = Color(0xFF9A210C);

  // §2.5 語意色
  static const successFg = Color(0xFF1F4E27);
  static const successBg = Color(0xFFDFF6DE);
  static const warnFg = Color(0xFF6F3500);
  static const warnBg = Color(0xFFFFE9CB);
  static const infoFg = Color(0xFF33465F);
  static const infoBg = Color(0xFFE0F1FF);

  // §2.7
  static const avatarBg = Color(0xFFFFECC9);
  static const avatarFg = Color(0xFF584200);
}

/// §2.6 事件分類。dot 只能放在 card / nest 底上。
/// 值與 api.md（GET /events）的 `type` 字串一一對應。
enum EventCategory {
  diet,
  activity,
  sleep,
  medication,
  wellbeing,
  other;

  /// api.md 的 `type` 字串 → 分類；未知或 null 一律歸 other（承 api.md 分類原則）。
  static EventCategory fromType(String? type) => switch (type) {
        'diet' => diet,
        'activity' => activity,
        'sleep' => sleep,
        'medication' => medication,
        'wellbeing' => wellbeing,
        _ => other,
      };
}

extension EventCategoryStyle on EventCategory {
  String get label => switch (this) {
        EventCategory.diet => '飲食',
        EventCategory.activity => '活動',
        EventCategory.sleep => '睡眠',
        EventCategory.medication => '用藥',
        EventCategory.wellbeing => '身心',
        EventCategory.other => '其他',
      };

  /// 深階文字色，紙底上皆 >=7:1
  Color get fg => switch (this) {
        EventCategory.diet => const Color(0xFF584200),
        EventCategory.activity => const Color(0xFF1F4E27),
        EventCategory.sleep => const Color(0xFF453F6D),
        EventCategory.medication => const Color(0xFF7D281F),
        EventCategory.wellbeing => const Color(0xFF7D281F),
        EventCategory.other => const Color(0xFF4F4335),
      };

  Color get bg => switch (this) {
        EventCategory.diet => const Color(0xFFFBEEC9),
        EventCategory.activity => const Color(0xFFDFF6DE),
        EventCategory.sleep => const Color(0xFFEAEDFF),
        EventCategory.medication => const Color(0xFFFFE5E1),
        EventCategory.wellbeing => const Color(0xFFFFE5E1),
        EventCategory.other => const Color(0xFFF2ECE1),
      };

  Color get dot => switch (this) {
        EventCategory.diet => const Color(0xFFA78100),
        EventCategory.activity => const Color(0xFF4D9351),
        EventCategory.sleep => const Color(0xFF676BA5),
        EventCategory.medication => const Color(0xFFC25D58),
        EventCategory.wellbeing => const Color(0xFFC25D58),
        EventCategory.other => const Color(0xFF4F4335),
      };

  /// §9 顏色不得單用承載資訊；時間軸圓點另以形狀區分
  BoxShape get dotShape =>
      this == EventCategory.diet ? BoxShape.rectangle : BoxShape.circle;
}

/// §4 間距（收斂為五階）
abstract final class AppSpacing {
  static const xs = 4.0;
  static const sm = 8.0;
  static const md = 12.0;
  static const lg = 16.0;
  static const xl = 24.0;

  static const cardMargin = EdgeInsets.all(16);
  static const pageBody = EdgeInsets.symmetric(vertical: 32, horizontal: 16);
}

/// §4 圓角
abstract final class AppRadius {
  static const badge = Radius.circular(8);
  static const field = Radius.circular(12);
  static const card = Radius.circular(16);
  static const cardLarge = Radius.circular(20);
  static const pill = Radius.circular(999);
  static const bubbleAi = BorderRadius.only(
      topLeft: Radius.circular(18),
      topRight: Radius.circular(18),
      bottomRight: Radius.circular(18),
      bottomLeft: Radius.circular(4));
  static const bubbleElder = BorderRadius.only(
      topLeft: Radius.circular(18),
      topRight: Radius.circular(18),
      bottomRight: Radius.circular(4),
      bottomLeft: Radius.circular(18));
  static const voicePanel = BorderRadius.only(
      topLeft: Radius.circular(26), topRight: Radius.circular(26));
}

/// §4 陰影。blurRadius 已從 CSS 收斂微調，勿再照抄 CSS 數值。
abstract final class AppShadows {
  static const card = [
    BoxShadow(color: Color(0x0F5A4632), blurRadius: 6, offset: Offset(0, 2))
  ];
  static const cardRaised = [
    BoxShadow(color: Color(0x145A4632), blurRadius: 9, offset: Offset(0, 3))
  ];
  static const bubble = [
    BoxShadow(color: Color(0x125A4632), blurRadius: 6, offset: Offset(0, 2))
  ];
  static const voicePanel = [
    BoxShadow(color: Color(0x123D3229), blurRadius: 15, offset: Offset(0, -4))
  ];
  static const mic = [
    BoxShadow(color: Color(0x59D15640), blurRadius: 13, offset: Offset(0, 6))
  ];
  static const toast = [
    BoxShadow(color: Color(0x40000000), blurRadius: 15, offset: Offset(0, 6))
  ];
}

/// §3 字體家族依字級分工：>=24sp 用 Noto Serif TC，<24sp 用 Noto Sans TC。
/// 永遠走 Theme.of(context).textTheme，不要寫死 fontSize。
abstract final class AppTypography {
  static TextStyle _serif(double size, FontWeight w) => GoogleFonts.notoSerifTc(
      fontSize: size, fontWeight: w, height: 1.5, color: AppColors.ink);
  static TextStyle _sans(double size, FontWeight w) => GoogleFonts.notoSansTc(
      fontSize: size, fontWeight: w, height: 1.55, color: AppColors.ink);

  static final textTheme = TextTheme(
    // 農民曆巨大日期（§7.2）— 行高 .78
    displayLarge: GoogleFonts.notoSerifTc(
        fontSize: 66,
        fontWeight: FontWeight.w900,
        height: .78,
        color: AppColors.accentText),
    displayMedium: _serif(46, FontWeight.w900),
    // 長者模式標題／狀態大字
    headlineLarge: _serif(32, FontWeight.w900),
    headlineMedium: _serif(26, FontWeight.w900),
    // 長者模式內文下限（§3 硬性 24）
    headlineSmall: _serif(24, FontWeight.w700),
    // 照護者模式
    titleLarge: _sans(22, FontWeight.w700),
    titleMedium: _sans(18, FontWeight.w700),
    titleSmall: _sans(16, FontWeight.w700),
    bodyLarge: _sans(17, FontWeight.w500),
    bodyMedium: _sans(15, FontWeight.w500),
    bodySmall: _sans(13, FontWeight.w500), // 照護者字級下限，不可再小
    labelLarge: _sans(16, FontWeight.w700),
    labelMedium: _sans(14, FontWeight.w700),
    labelSmall: _sans(13, FontWeight.w700),
  );
}

/// §10 強制淺色，不提供深色模式。
ThemeData buildAppTheme() {
  final base = ThemeData(brightness: Brightness.light, useMaterial3: true);
  return base.copyWith(
    scaffoldBackgroundColor: AppColors.app,
    textTheme: AppTypography.textTheme,
    colorScheme: base.colorScheme.copyWith(
      primary: AppColors.accentText,
      onPrimary: Colors.white,
      surface: AppColors.card,
      onSurface: AppColors.ink,
      error: const Color(0xFF7D281F),
    ),
    dividerColor: AppColors.divider,
    splashFactory: InkRipple.splashFactory,
  );
}
