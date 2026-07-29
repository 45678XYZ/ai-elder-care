// 暖紙手帳 — 單一真實來源的 Theme 實作
// 對映 SKILL.md §2 顏色 / §3 字體 / §4 間距圓角陰影
// 字體：assets/fonts/ 的 Noto Serif TC／Noto Sans TC（見 pubspec）

import 'package:flutter/material.dart';

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

  /// 佔位文字。對欄位底 4.9:1，過 4.5:1。
  /// [chevron] 是裝飾用的淡色，拿來當文字色一律不合格。
  static const hint = Color(0xFF7A6A55);

  // §2.4 朱紅 accent
  static const accentText = Color(0xFFAF3723); // 供 >=24sp 文字、白字實心底
  static const accent = Color(0xFFD15640); // 非文字：外框、外環、進度
  static const accentPressed = Color(0xFF9A210C);

  /// 日曆平日藍。台灣日曆的慣例是假日紅、平日藍——牌面依當天是不是假日切換主色。
  /// 明度與 [accentText] 相當，在紙底上對比 >=7:1。
  static const calendarWeekday = Color(0xFF1B4E8C);

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

/// §3 字體家族依**用途**分工，不是依字級：
///
/// - **黑體（Noto Sans TC）** — 標題、大數字、內文、按鈕、輸入框，所有 UI 文字。
/// - **襯線（Noto Serif TC）** — 只留農民曆牌面，見 [AlmanacTypography]。
///
/// 這條規則收斂過兩次。最早是「>=24sp 一律襯線」，但長者模式內文全在 24sp 以上，
/// 等於整個 App 都是明體；接著收成「標題與大數字襯線」，實機看標題級的中文還是
/// 同一個問題，只是輕一點。明體橫畫細，對比敏感度下降的長輩讀起來是實質負擔；
/// 西文（如 email）用襯線渲染也跟中文不搭。手帳感由牌面、紙感底與朱紅點綴撐著，
/// 不必靠 UI 文字的字形。
///
/// 永遠走 Theme.of(context).textTheme，不要寫死 fontSize。
abstract final class AppTypography {
  /// 字檔打包在 assets/fonts/（見 pubspec）。**不用 google_fonts 執行期下載**——
  /// 下載版第一次啟動與離線時會先用系統字頂著、載完才跳字，而且各家 Android
  /// 的預設中文字不同，畫面會因機而異。
  ///
  /// [serifFamily] 現在只有牌面在用，但常數留在這裡：牌面的字級組是照這一份
  /// token 表定的，字體家族跟著一起放才找得到。
  static const serifFamily = 'NotoSerifTC';
  static const sansFamily = 'NotoSansTC';

  /// [height] 只有標題那兩階會覆寫：46/32 沿用收斂前的 1.5，行距跟著字級一起
  /// 放大會讓標題散開。
  static TextStyle _sans(double size, FontWeight w, {double height = 1.55}) =>
      TextStyle(
          fontFamily: sansFamily,
          fontSize: size,
          fontWeight: w,
          height: height,
          color: AppColors.ink);

  static final textTheme = TextTheme(
    // 大數字（麥克風狀態等）— 行高 .78
    displayLarge: const TextStyle(
        fontFamily: sansFamily,
        fontSize: 66,
        fontWeight: FontWeight.w900,
        height: .78,
        color: AppColors.accentText),
    displayMedium: _sans(46, FontWeight.w900, height: 1.5),
    // 長者模式標題
    headlineLarge: _sans(32, FontWeight.w900, height: 1.5),
    // 狀態大字（麥克風狀態等）：是 UI 而非標題，走黑體
    headlineMedium: _sans(26, FontWeight.w900),
    // 長者模式內文下限（§3 硬性 24）：內文與按鈕、輸入框都用這階，走黑體
    headlineSmall: _sans(24, FontWeight.w700),
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

/// 農民曆牌面字級（MASTER.md「農民曆牌面」那張表）。
///
/// 這裡放的是**比例**，不是最終尺寸：表上的數值是牌面寬度等於 [refWidth] 時的
/// 渲染尺寸，實際渲染由 `AlmanacFace` 整組乘上 `牌面寬度 / refWidth`。
/// 六個元素是一組互相依賴的比例，缺一個或單獨改一個就不是撕曆的樣子了——
/// 之前小卡／過場／放大檢視各寫一套絕對字級，三個地方的版面才會各長各的。
///
/// 牌面是**全 App 唯一用襯線的地方**（見 [AppTypography] §3 的收斂過程）：整組
/// 六個元素都是曆書的一部分，維持襯線才有黃曆的樣子，即使字級小於 24。
///
/// 顏色由呼叫端決定（台灣日曆慣例：假日朱紅、平日藍），此處不預設。
abstract final class AlmanacTypography {
  static TextStyle serif(double size, FontWeight weight,
          {double? height, double? letterSpacing}) =>
      TextStyle(
        fontFamily: AppTypography.serifFamily,
        fontSize: size,
        fontWeight: weight,
        height: height,
        letterSpacing: letterSpacing,
      );

  /// 比例的基準寬度：390 螢幕扣掉頁面 16 邊距與卡片 16 內距後的牌面內寬。
  /// v3 原檔的字級就是照這個寬度定的。
  static const double refWidth = 326;

  /// 國曆年「2026」
  static const double year = 28;

  /// 干支「歲次丙午年」；節氣膠囊也用這一級。
  static const double ganZhi = 24;

  /// 月份數字「7」與底下的「月」
  static const double monthNumber = 40;
  static const double monthLabel = 24;

  /// 直排農曆「農曆六月十五」。字距在直排是**字與字的垂直間隔**，
  /// 由排版加 gap 實作，不是 letterSpacing。
  static const double lunar = 30;
  static const double lunarGap = 8;

  /// 大日期「28」。行高壓過 1，數字才不會在中段裡浮著。
  static const double day = 200;
  static const double dayHeight = 0.78;

  /// 大日期往上推的比例（字級的幾成）。文字框下緣留著降部空間、數字用不到，
  /// 框置中會讓數字看起來偏下、貼著「星期」，往上推才是視覺置中。
  static const double dayLift = 0.08;

  /// 星期「星期二」——靠字距撐開。
  static const double weekday = 42;
  static const double weekdaySpacing = 16;

  /// 首頁小卡是半版寬，等比縮下去會掉到讀不出來，這幾個是縮不下去的下限。
  /// 只影響字級，不影響版面——所有尺寸的牌面長得一模一樣。
  static const double minHeader = 12;
  static const double minLunar = 11;
  static const double minWeekday = 15;
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
