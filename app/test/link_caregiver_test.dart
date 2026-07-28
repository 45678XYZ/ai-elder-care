import 'package:ai_elder_care/elder/screens/link_caregiver_screen.dart';
import 'package:ai_elder_care/shared/services/session_store.dart';
import 'package:ai_elder_care/theme/app_theme.dart';
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:shared_preferences/shared_preferences.dart';

/// 連結家人這一頁的行為。
///
/// 這是照護者拿長輩手機操作、且會改變帳號歸屬的流程，出錯的代價比其他畫面高：
/// 加錯了長輩自己不會發現，所以每一種送出結果都要有看得懂的回饋。
void main() {
  setUp(() {
    SharedPreferences.setMockInitialValues({});
    AppSession.instance.linkedCaregiverIds = const [];
  });

  Future<void> pump(
    WidgetTester tester, {
    double textScale = 1.0,
    Size size = const Size(390, 844),
  }) async {
    tester.view
      ..physicalSize = size
      ..devicePixelRatio = 1.0;
    addTearDown(tester.view.reset);

    await tester.pumpWidget(
      MaterialApp(
        theme: buildAppTheme(),
        home: MediaQuery(
          data: MediaQueryData(
            size: size,
            textScaler: TextScaler.linear(textScale),
          ),
          child: const LinkCaregiverScreen(),
        ),
      ),
    );
    await tester.pumpAndSettle();
  }

  testWidgets('畫得出來', (tester) async {
    await pump(tester);
    expect(find.text('連結家人'), findsOneWidget);
    expect(find.text('加入'), findsOneWidget);
  });

  testWidgets('兩倍字級不 overflow', (tester) async {
    await pump(tester, textScale: 2.0);
    expect(tester.takeException(), isNull);
  });

  testWidgets('空白送出會說要填', (tester) async {
    await pump(tester);
    await tester.tap(find.text('加入'));
    await tester.pumpAndSettle();

    expect(find.text('請先輸入 ID'), findsOneWidget);
    expect(AppSession.instance.linkedCaregiverIds, isEmpty);
  });

  testWidgets('輸入後加入，會進清單也會有成功回饋', (tester) async {
    await pump(tester);
    await tester.enterText(find.byType(TextField), 'CG12345');
    await tester.tap(find.text('加入'));
    await tester.pumpAndSettle();

    expect(AppSession.instance.linkedCaregiverIds, ['CG12345']);
    expect(find.text('連結成功'), findsOneWidget);
    // 清單裡看得到，且輸入框已清空可以再加下一位。
    expect(find.text('CG12345'), findsOneWidget);
    expect(find.text('已經連結的家人'), findsOneWidget);
  });

  testWidgets('可以連結多位家人', (tester) async {
    await pump(tester);
    for (final id in ['CG00001', 'CG00002']) {
      await tester.enterText(find.byType(TextField), id);
      await tester.tap(find.text('加入'));
      await tester.pumpAndSettle();
    }

    expect(AppSession.instance.linkedCaregiverIds, ['CG00001', 'CG00002']);
  });

  testWidgets('同一個 ID 不會重複加，而且要講清楚為什麼', (tester) async {
    await pump(tester);
    for (var i = 0; i < 2; i++) {
      await tester.enterText(find.byType(TextField), 'CG12345');
      await tester.tap(find.text('加入'));
      await tester.pumpAndSettle();
    }

    expect(AppSession.instance.linkedCaregiverIds, ['CG12345']);
    expect(find.text('這個 ID 已經連結過了'), findsOneWidget);
  });

  testWidgets('ID 前後空白不算數', (tester) async {
    await pump(tester);
    await tester.enterText(find.byType(TextField), '  CG12345  ');
    await tester.tap(find.text('加入'));
    await tester.pumpAndSettle();

    expect(AppSession.instance.linkedCaregiverIds, ['CG12345']);
  });

  testWidgets('已連結的家人重開頁面還在', (tester) async {
    AppSession.instance.linkedCaregiverIds = const ['CG99999'];
    await pump(tester);

    expect(find.text('CG99999'), findsOneWidget);
  });
}
