import 'package:e_hakka_care/theme/app_theme.dart';
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';

/// 事件分類的視覺 token（`EventCategoryStyle`）。
///
/// 這組色出過兩次事，都是「畫面上分不出是哪一類」：
/// 1. `medication` 與 `wellbeing` 共用同一個紅 `#7D281F`，摘要與時間軸上完全一樣。
/// 2. `safety` 一度接 §2.5 的警示橘，夾在紅（用藥）與琥珀（飲食）之間，三個深色階看起來同一色。
///
/// 兩次都是 review 時用眼睛看沒看出來、實機才發現，所以把「不重複、對比夠、色相分得開」
/// 釘成測試。分類名稱本身是文字，色彩不是唯一線索，但色彩錯了照護者掃過去就會讀錯類別。
void main() {
  /// §2.1 最深的紙底。所有分類文字色的對比都以它為基準（最壞情況）。
  const darkestPaper = Color(0xFFE4DCCB);

  /// WCAG 對比。兩色順序無關。
  double contrast(Color a, Color b) {
    final la = a.computeLuminance();
    final lb = b.computeLuminance();
    final hi = la > lb ? la : lb;
    final lo = la > lb ? lb : la;
    return (hi + 0.05) / (lo + 0.05);
  }

  /// 低彩度的「其他」沒有有意義的色相，不參與色相間距檢查。
  final hued = EventCategory.values
      .where((c) => c != EventCategory.other)
      .toList(growable: false);

  /// 色相在色環上的最短距離（度）。
  double hueGap(Color a, Color b) {
    final d =
        (HSVColor.fromColor(a).hue - HSVColor.fromColor(b).hue).abs() % 360;
    return d > 180 ? 360 - d : d;
  }

  test('七類的文字色不重複', () {
    final fgs = EventCategory.values.map((c) => c.fg).toList();
    expect(fgs.toSet().length, EventCategory.values.length,
        reason: '有兩類共用同一個文字色：$fgs');
  });

  test('七類的底色不重複', () {
    final bgs = EventCategory.values.map((c) => c.bg).toList();
    expect(bgs.toSet().length, EventCategory.values.length,
        reason: '有兩類共用同一個底色：$bgs');
  });

  test('七類的圓點色不重複', () {
    final dots = EventCategory.values.map((c) => c.dot).toList();
    expect(dots.toSet().length, EventCategory.values.length,
        reason: '有兩類共用同一個圓點色：$dots');
  });

  test('文字色對最深紙底都 >=7:1', () {
    for (final c in EventCategory.values) {
      expect(contrast(c.fg, darkestPaper), greaterThanOrEqualTo(7.0),
          reason: '${c.label} 的文字色對比不足');
    }
  });

  test('膠囊上的文字對自己的底色都 >=7:1', () {
    for (final c in EventCategory.values) {
      expect(contrast(c.fg, c.bg), greaterThanOrEqualTo(7.0),
          reason: '${c.label} 的膠囊字對底色對比不足');
    }
  });

  test('有彩度的六類色相兩兩至少差 35 度', () {
    for (var i = 0; i < hued.length; i++) {
      for (var j = i + 1; j < hued.length; j++) {
        final a = hued[i];
        final b = hued[j];
        expect(hueGap(a.fg, b.fg), greaterThanOrEqualTo(35.0),
            reason: '${a.label} 與 ${b.label} 的色相太接近，掃過去會讀錯類別');
      }
    }
  });

  test('圓點形狀給了相鄰色相第二層線索', () {
    // 顏色不得單用承載資訊（§9）。圓點只有 14px，色相分得開也可能看不準。
    expect(EventCategory.diet.dotShape, EventDotShape.square);
    expect(EventCategory.safety.dotShape, EventDotShape.diamond);
  });

  test('標籤都是看得懂的中文，沒有漏掉新分類', () {
    for (final c in EventCategory.values) {
      expect(c.label.trim(), isNotEmpty);
    }
    // 順序即摘要 sections 的呈現順序，與 api.md 的 EventType 對齊
    expect(EventCategory.values.map((c) => c.name).toList(), [
      'diet',
      'activity',
      'sleep',
      'medication',
      'wellbeing',
      'safety',
      'other',
    ]);
  });
}
