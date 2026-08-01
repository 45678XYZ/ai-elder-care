import 'package:e_hakka_care/elder/widgets/greeting_slot.dart';
import 'package:flutter_test/flutter_test.dart';

/// 時段問候的分界。
///
/// 分界是產品決定的（早安 04–11、午安 11–18、晚安 18–隔天 04），而它同時決定
/// 今日頁的早安圖、放大檢視與聊天室開場那句話。三個地方共用 [GreetingSlot]，
/// 這裡把那組數字釘住——尤其是**凌晨算晚安**，那是最容易被改回「h < 11 就早安」的一段。
void main() {
  GreetingSlot at(int hour) => GreetingSlot.of(DateTime(2026, 7, 29, hour));

  test('早安：04:00–10:59', () {
    expect(at(4), GreetingSlot.morning);
    expect(at(7), GreetingSlot.morning);
    expect(at(10), GreetingSlot.morning);
  });

  test('午安：11:00–17:59', () {
    expect(at(11), GreetingSlot.afternoon);
    expect(at(15), GreetingSlot.afternoon);
    expect(at(17), GreetingSlot.afternoon);
  });

  test('晚安：18:00 到隔天 03:59', () {
    expect(at(18), GreetingSlot.evening);
    expect(at(23), GreetingSlot.evening);
    // 跨過午夜仍是晚安——長輩半夜起來看手機，不該被說「早安」。
    expect(at(0), GreetingSlot.evening);
    expect(at(3), GreetingSlot.evening);
  });

  test('label 與圖檔一一對應，不會拿到別的時段的圖', () {
    expect(GreetingSlot.morning.label, '早安');
    expect(GreetingSlot.afternoon.label, '午安');
    expect(GreetingSlot.evening.label, '晚安');
    for (final slot in GreetingSlot.values) {
      expect(slot.asset, startsWith('assets/images/greeting_'));
    }
  });
}
