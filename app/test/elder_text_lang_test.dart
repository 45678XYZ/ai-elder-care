import 'package:ai_elder_care/elder/widgets/lang_toggle.dart';
import 'package:ai_elder_care/shared/i18n/strings.dart';
import 'package:ai_elder_care/shared/models/elder.dart';
import 'package:ai_elder_care/shared/services/session_store.dart';
import 'package:ai_elder_care/theme/app_theme.dart';
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:shared_preferences/shared_preferences.dart';

/// 畫面文字的書寫語言（一般漢字 ↔ 客語漢字）。
///
/// 最要緊的一條是「兩顆鈕互不相干」：講客語的長輩不一定讀得懂客語漢字，把語音
/// 跟文字綁在一起會逼這種人二選一。所以這裡刻意驗兩個方向——切了文字不該動到
/// 語音，切了語音也不該動到文字。
void main() {
  const sub = 'sub-elder';

  setUp(() async {
    SharedPreferences.setMockInitialValues({});
    await AppSession.instance.loadForAccount(sub);
    AppSession.instance
      ..elders = const []
      ..selectedElderId = null;
  });

  Future<void> pumpBoth(WidgetTester tester) async {
    await tester.pumpWidget(MaterialApp(
      theme: buildAppTheme(),
      home: const Scaffold(
        body: Column(children: [ElderLangToggle(), ElderTextLangToggle()]),
      ),
    ));
    await tester.pumpAndSettle();
  }

  testWidgets('切畫面文字不會動到說話的語言', (tester) async {
    await pumpBoth(tester);
    await tester.tap(find.text('客語漢字'));
    await tester.pumpAndSettle();

    expect(AppSession.instance.isHakkaText, isTrue);
    expect(AppSession.instance.isHakka, isFalse,
        reason: '講客語跟讀得懂客語漢字是兩件事，不可以互相帶動');
  });

  testWidgets('切說話的語言不會動到畫面文字', (tester) async {
    await pumpBoth(tester);
    await tester.tap(find.text('客語'));
    await tester.pumpAndSettle();

    expect(AppSession.instance.isHakka, isTrue);
    expect(AppSession.instance.isHakkaText, isFalse);
  });

  testWidgets('選了客語漢字之後，介面文字換掉', (tester) async {
    expect(t('今天的安排'), '今天的安排');

    await AppSession.instance.setTextLang('hak');
    expect(t('今天的安排'), '今晡日个安排');
    expect(t('聊天'), '打嘴鼓');
  });

  testWidgets('缺譯的句子原樣留在華語，不會變空白', (tester) async {
    await AppSession.instance.setTextLang('hak');
    // 對照表裡沒有的句子——新增畫面文字但還沒送翻時就是這個情況。
    expect(t('這句話沒有客語漢字'), '這句話沒有客語漢字', reason: '缺譯要退回華語，長輩至少讀得到內容，不能變成空白');
    for (final zh in missingFromHakka) {
      expect(t(zh), zh);
    }
  });

  testWidgets('登出確認框的字也換得掉——最後補譯的那一組', (tester) async {
    await AppSession.instance.setTextLang('hak');
    expect(t('要登出嗎？'), '愛登出無？');
    expect(t('不要'), '莫');
    expect(missingFromHakka, isEmpty, reason: '長者端介面應已全數譯完');
  });

  testWidgets('帶變數的句子換語言後變數還在', (tester) async {
    await AppSession.instance.setTextLang('hak');
    final s = t1('「{}」已記錄完成', '吃血壓藥');
    expect(s.contains('吃血壓藥'), isTrue, reason: '變數掉了的話長輩不知道是哪一件事完成了');
    expect(s.contains('{}'), isFalse, reason: '佔位符不可以留在畫面上');
  });

  testWidgets('選過的書寫語言在重新登入之後還在', (tester) async {
    await AppSession.instance.setTextLang('hak');
    await AppSession.instance.clearForAccount(sub);
    await AppSession.instance.loadForAccount(sub);

    expect(AppSession.instance.isHakkaText, isTrue);
  });

  testWidgets('切了文字語言，另一顆鈕的標題也要跟著換', (tester) async {
    // 這一條擋的是 const 陷阱：兩顆鈕在今日頁都是 `const`，而 const widget 會被
    // 正規化成同一個實例，父層 setState 重建時 Flutter 比到 identical(new, old)
    // 就整棵子樹跳過。症狀是「我說的話」四個字永遠不變，但旁邊那顆自己會變——
    // 看起來像翻譯漏了，其實是根本沒重畫。
    await pumpBoth(tester);
    expect(find.text('我說的話'), findsOneWidget);

    await tester.tap(find.text('客語漢字'));
    await tester.pumpAndSettle();

    expect(find.text('𠊎講个話'), findsOneWidget, reason: '語音鈕的標題要跟著換');
    expect(find.text('我說的話'), findsNothing);
  });

  testWidgets('照護者設的語音偏好不會牽動畫面文字', (tester) async {
    AppSession.instance
      ..elders = const [
        Elder(elderId: 'eld_1', name: '陳阿蘭', langPreference: 'hak'),
      ]
      ..selectedElderId = 'eld_1';

    expect(AppSession.instance.isHakka, isTrue, reason: '語音照他設的走');
    expect(AppSession.instance.isHakkaText, isFalse,
        reason: '書寫語言後端沒有這個欄位，只有長輩自己設得了');
  });
}
