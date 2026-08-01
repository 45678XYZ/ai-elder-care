import 'package:flutter/material.dart';

import '../../shared/i18n/strings.dart';

/// 時段問候（早安／午安／晚安）與對應的早安圖。
///
/// 分界照長輩的實際作息定，不是把一天平均切三份：
///
/// | 時段 | 範圍（台灣時間） |
/// |---|---|
/// | 早安 | 04:00–10:59 |
/// | 午安 | 11:00–17:59 |
/// | 晚安 | 18:00–03:59（跨過午夜） |
///
/// 凌晨那段刻意歸在晚安：長輩半夜三點起來看手機，該收到的是「晚安」而不是「早安」。
///
/// 三個畫面共用同一份（今日頁的撕曆、放大檢視、聊天室的開場問候）。原本各寫一份
/// `h < 11 ? … : …`，改分界時只會改到其中一處，同一個時間點在兩個畫面說不同的話。
enum GreetingSlot {
  morning('早安', Icons.wb_twilight, 'assets/images/greeting_morning.jpg'),
  afternoon(
      '午安', Icons.wb_sunny_outlined, 'assets/images/greeting_afternoon.png'),
  evening(
      '晚安', Icons.nightlight_outlined, 'assets/images/greeting_evening.png');

  const GreetingSlot(this.label, this.icon, this.asset);

  /// 華語原文。**同時是 i18n 對照表的 key**，所以不隨長輩選的書寫語言變動——
  /// 要顯示在畫面上的請用 [text]。
  final String label;

  /// 畫面上實際顯示的問候語，會依長輩選的書寫語言換成客語漢字。
  String get text => t(label);

  /// 沒有圖檔時的替代圖示（早安圖找不到就退回色塊加大字）。
  final IconData icon;

  /// `assets/images/greeting_*`。副檔名依素材而定（morning 是 .jpg、其餘 .png）；
  /// 換素材若連格式一起換，只要改這裡一處。
  final String asset;

  /// [now] 落在哪一個時段。
  static GreetingSlot of(DateTime now) {
    final h = now.hour;
    // 00:00–03:59 先擋掉，否則會被後面的 `h < 11` 當成早安。
    if (h < 4) return evening;
    if (h < 11) return morning;
    if (h < 18) return afternoon;
    return evening;
  }
}
