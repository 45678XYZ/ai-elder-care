// 基本 smoke test：確認 App 能建置，且未登入時落在登入頁。
//
// 落點由 app_router 的 redirect 決定，不是各畫面自己導的，所以這裡從 App 根部起跑
// 才驗得到「沒登入就進不去」這條規則。

import 'package:flutter_test/flutter_test.dart';
import 'package:shared_preferences/shared_preferences.dart';

import 'package:e_hakka_care/main.dart';

void main() {
  setUp(() {
    // 沒有任何持久化狀態 = 全新安裝、未登入。
    SharedPreferences.setMockInitialValues({});
  });

  testWidgets('未登入時顯示登入頁', (WidgetTester tester) async {
    await tester.pumpWidget(AiElderCareApp());
    await tester.pumpAndSettle();

    // 登入頁的標題與主要按鈕（標題與按鈕同字，所以是兩個）
    expect(find.text('登入'), findsNWidgets(2));
    expect(find.text('還沒有帳號？註冊'), findsOneWidget);
  });
}
