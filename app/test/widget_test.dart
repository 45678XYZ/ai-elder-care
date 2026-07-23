// 基本 smoke test：確認 App 能建置並顯示問答畫面。

import 'package:flutter_test/flutter_test.dart';

import 'package:ai_elder_care/main.dart';

void main() {
  testWidgets('App 啟動後顯示對話畫面', (WidgetTester tester) async {
    await tester.pumpWidget(const AiElderCareApp());
    // 對話畫面標題（見 ChatScreen 的 AppBar）
    expect(find.text('衛教問答（PoC）'), findsOneWidget);
  });
}
