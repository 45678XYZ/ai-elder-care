import 'dart:math' as math;
import 'dart:ui';

import 'package:e_hakka_care/theme/app_theme.dart';
import 'package:flutter_test/flutter_test.dart';

/// 可點選元件的外框對比。
///
/// 起因：選項膠囊未選取時用的是 `AppColors.border`（MASTER.md 的「邊框／輸入框線」），
/// 壓在紙色背景上只有 1.3:1——看不出那裡有一顆可以按的東西，也看不出選項之間的界線。
/// WCAG 1.4.11 對「識別 UI 元件所必需的邊界」要求 3:1。
///
/// 這支測試釘住的是**用途的區分**：`border` 可以繼續當裝飾框線，但一旦要承載
/// 「這是可互動元件的邊界」，就得走 [AppColors.borderInteractive]。
void main() {
  double relativeLuminance(Color c) {
    double channel(double v) => v <= 0.03928
        ? v / 12.92
        : math.pow((v + 0.055) / 1.055, 2.4).toDouble();
    return 0.2126 * channel(c.r) +
        0.7152 * channel(c.g) +
        0.0722 * channel(c.b);
  }

  double contrast(Color a, Color b) {
    final la = relativeLuminance(a);
    final lb = relativeLuminance(b);
    final hi = math.max(la, lb);
    final lo = math.min(la, lb);
    return (hi + 0.05) / (lo + 0.05);
  }

  // 未選取的膠囊會出現在這幾種底色上。
  const backgrounds = <String, Color>{
    'app': AppColors.app,
    'card': AppColors.card,
    'cardAlt': AppColors.cardAlt,
    'nest': AppColors.nest,
  };

  group('borderInteractive', () {
    test('在所有紙色底上都達到 WCAG 1.4.11 的 3:1', () {
      backgrounds.forEach((name, bg) {
        final ratio = contrast(AppColors.borderInteractive, bg);
        expect(ratio, greaterThanOrEqualTo(3.0),
            reason: '$name 底上只有 ${ratio.toStringAsFixed(2)}:1');
      });
    });

    test('比 inkSecondary 淡——外框不該搶過文字', () {
      // 文字走 inkSecondary，外框比它淡一階，層次才對；一樣深會像實心按鈕。
      expect(relativeLuminance(AppColors.borderInteractive),
          greaterThan(relativeLuminance(AppColors.inkSecondary)));
    });
  });

  group('分類 chip 選取時的外框', () {
    // 選取時填的是 category.bg，但七個 bg 對頁面底都只有 1.0:1——當外框等於沒有。
    // 其他六類還靠色相分得出來，`other` 是低彩度（#F2ECE1 vs app #F3ECDD），
    // 明度與色相都一樣，選了跟沒選看不出差別。外框因此改走 category.dot。
    test('每一類的 dot 對頁面底都達 3:1', () {
      for (final c in EventCategory.values) {
        final ratio = contrast(c.dot, AppColors.app);
        expect(ratio, greaterThanOrEqualTo(3.0),
            reason: '${c.label} 的 dot 只有 ${ratio.toStringAsFixed(2)}:1');
      }
    });

    test('迴歸：bg 當外框是不夠的（尤其 other）', () {
      for (final c in EventCategory.values) {
        expect(contrast(c.bg, AppColors.app), lessThan(3.0),
            reason: '${c.label}：若哪天 bg 變得夠深，這裡的取捨要重新檢討');
      }
    });
  });

  group('迴歸：原本的 border 撐不起可互動元件', () {
    // 留著這條是為了說明「為什麼不能直接用 border」——它本身沒壞，只是用途不同。
    test('border 在紙色底上不足 3:1，不可用於可點元件邊界', () {
      backgrounds.forEach((name, bg) {
        expect(contrast(AppColors.border, bg), lessThan(3.0),
            reason: '$name：若哪天 border 變深到 3:1 以上，'
                '這兩個 token 的分工就該重新檢討');
      });
    });
  });
}
