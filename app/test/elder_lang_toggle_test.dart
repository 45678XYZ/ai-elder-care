import 'package:ai_elder_care/elder/widgets/lang_toggle.dart';
import 'package:ai_elder_care/shared/models/elder.dart';
import 'package:ai_elder_care/shared/services/session_store.dart';
import 'package:ai_elder_care/theme/app_theme.dart';
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:shared_preferences/shared_preferences.dart';

/// 長者自己切換說話語言。
///
/// 這裡最要釘住的不是「按下去有沒有變」，而是**長者選華語時切得回來**。
/// [AppSession.isHakka] 原本的寫法是「lang=='hak' 就客語，否則看照護者設的
/// lang_preference」——照護者設客語時，長者不管怎麼按華語都會被那一層蓋回去，
/// 按了沒反應。這種壞法在畫面上完全看不出來（鈕的選取狀態是對的，只有實際
/// 走哪條輸入路徑是錯的），所以要有測試盯著。
void main() {
  const sub = 'sub-elder';

  setUp(() async {
    SharedPreferences.setMockInitialValues({});
    await AppSession.instance.loadForAccount(sub);
    AppSession.instance
      ..elders = const []
      ..selectedElderId = null;
  });

  /// 照護者在後端設好的長者（`lang_preference` 由他決定）。
  void caregiverSet(String langPreference) {
    AppSession.instance
      ..elders = [
        Elder(
          elderId: 'eld_1',
          name: '陳阿蘭',
          langPreference: langPreference,
        ),
      ]
      ..selectedElderId = 'eld_1';
  }

  Future<void> pump(WidgetTester tester) async {
    await tester.pumpWidget(MaterialApp(
      theme: buildAppTheme(),
      home: const Scaffold(body: ElderLangToggle()),
    ));
    await tester.pumpAndSettle();
  }

  testWidgets('兩個選項都在，文字是中文／客語', (tester) async {
    await pump(tester);
    expect(find.text('中文'), findsOneWidget);
    expect(find.text('客語'), findsOneWidget);
  });

  testWidgets('按客語之後走錄音那條路', (tester) async {
    await pump(tester);
    await tester.tap(find.text('客語'));
    await tester.pumpAndSettle();

    expect(AppSession.instance.lang, 'hak');
    expect(AppSession.instance.isHakka, isTrue);
  });

  testWidgets('照護者設了客語，長者仍切得回中文', (tester) async {
    caregiverSet('hak');
    await pump(tester);
    expect(AppSession.instance.isHakka, isTrue, reason: '沒選過時以照護者設的為準');

    await tester.tap(find.text('中文'));
    await tester.pumpAndSettle();

    expect(AppSession.instance.isHakka, isFalse,
        reason: '長者自己選過就以他為準，不該被照護者那一層蓋回去');
  });

  testWidgets('沒選過時不搶照護者設的值', (tester) async {
    caregiverSet('hak');
    await pump(tester);
    // 只是把畫面畫出來、沒有按任何東西，不該把 lang 寫成預設的華語。
    expect(AppSession.instance.isHakka, isTrue);
  });

  testWidgets('選過的語言在重新登入之後還在', (tester) async {
    await pump(tester);
    await tester.tap(find.text('客語'));
    await tester.pumpAndSettle();

    await AppSession.instance.clearForAccount(sub);
    await AppSession.instance.loadForAccount(sub);

    expect(AppSession.instance.isHakka, isTrue, reason: '語言要記到下次啟動');
  });

  testWidgets('切換會廣播出去——聊天頁靠它重問麥克風權限', (tester) async {
    final seen = <int>[];
    void listener() => seen.add(AppSession.langRevision.value);
    AppSession.langRevision.addListener(listener);
    addTearDown(() => AppSession.langRevision.removeListener(listener));

    await pump(tester);
    await tester.tap(find.text('客語'));
    await tester.pumpAndSettle();

    expect(seen, isNotEmpty, reason: '沒廣播的話，聊天頁會拿著上一種語言的權限答案');
  });
}
