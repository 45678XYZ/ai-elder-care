// 基本 smoke test：確認 App 能建置並顯示首次設定畫面（S1）。

import 'package:flutter_test/flutter_test.dart';

import 'package:ai_elder_care/main.dart';

void main() {
  testWidgets('App 啟動後顯示首次設定畫面', (WidgetTester tester) async {
    await tester.pumpWidget(AiElderCareApp());
    // S1 setup 畫面標題
    expect(find.text('建立長輩的基本資料'), findsOneWidget);
  });
}
