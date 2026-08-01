import 'package:e_hakka_care/shared/widgets/sign_out_button.dart';
import 'package:e_hakka_care/theme/app_theme.dart';
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:go_router/go_router.dart';
import 'package:shared_preferences/shared_preferences.dart';

/// 登出。
///
/// 在這之前 [AuthService.signOut] 沒有任何 UI 呼叫端——換帳號的唯一辦法是清掉
/// App 資料，同一支手機給長輩與家人輪流用就卡住了。這支測試釘住兩件事：
/// 一定要問過才登出，以及登出後不能停在 App 內頁。
void main() {
  setUp(() async {
    SharedPreferences.setMockInitialValues({});
    // 先取一次把實例暖起來。SharedPreferences.getInstance() 第一次要走 method
    // channel，而 testWidgets 的時間是假的、真實事件迴圈是停的——signOut 在點下
    // 「登出」之後才第一次呼叫它的話，那個 Future 永遠等不到回應，後面的導頁就
    // 不會發生。預熱之後拿到的是已快取的實例，整條路徑留在假時間裡也走得完。
    await SharedPreferences.getInstance();
  });

  /// 把按鈕掛在一個有 router 的環境裡：登出後會 `context.go('/auth/sign-in')`。
  Future<GoRouter> pumpButton(WidgetTester tester,
      {bool elderMode = false}) async {
    final router = GoRouter(
      initialLocation: '/home',
      routes: [
        GoRoute(
          path: '/home',
          builder: (_, __) => Scaffold(
            body: Center(child: SignOutButton(elderMode: elderMode)),
          ),
        ),
        GoRoute(
          path: '/auth/sign-in',
          builder: (_, __) => const Scaffold(body: Text('登入頁')),
        ),
      ],
    );
    await tester.pumpWidget(
      MaterialApp.router(theme: buildAppTheme(), routerConfig: router),
    );
    await tester.pumpAndSettle();
    return router;
  }

  testWidgets('點下去先問過，不直接登出', (tester) async {
    final router = await pumpButton(tester);

    await tester.tap(find.text('登出'));
    await tester.pumpAndSettle();

    expect(find.text('要登出嗎？'), findsOneWidget);
    expect(router.state.uri.path, '/home', reason: '還沒確認就不該離開畫面');
  });

  testWidgets('選「不要」什麼都不做', (tester) async {
    final router = await pumpButton(tester);

    await tester.tap(find.text('登出'));
    await tester.pumpAndSettle();
    await tester.tap(find.text('不要'));
    await tester.pumpAndSettle();

    expect(find.text('要登出嗎？'), findsNothing);
    expect(router.state.uri.path, '/home');
  });

  testWidgets('確認後對話框關閉並開始登出', (tester) async {
    await pumpButton(tester);

    await tester.tap(find.text('登出'));
    await tester.pumpAndSettle();
    // 對話框裡的「登出」是第二個同名文字（按鈕本身也叫登出）。
    await tester.tap(find.widgetWithText(FilledButton, '登出'));
    await tester.pumpAndSettle();

    expect(find.text('要登出嗎？'), findsNothing);
  });

  // 「登出之後要導回登入頁」目前驗不到，原因不在這支測試：
  // AuthService.signOut() 第一件事是 await NotificationService.cancelAll()，
  // 而那個單例在 testWidgets 的假時間裡**永遠不會完成**（實測 pumpAndSettle 與
  // runAsync 之後都還沒回來，最後拋 LateError）。signOut 卡住，後面的 context.go
  // 就到不了。
  //
  // 這已經是同一個缺口第三次擋路了——另外兩個是「登出有沒有取消提醒」與「切換長輩
  // 有沒有重排」。解法都一樣：比照 AuthService.backend 把 NotificationService 抽成
  // 可替換的實作（見 notification_service.dart 的 TODO(test)）。在那之前這條要真機驗。

  testWidgets('長者模式的觸控區達 60dp（§3）', (tester) async {
    await pumpButton(tester, elderMode: true);

    final size = tester.getSize(find.byType(TextButton));
    expect(size.height, greaterThanOrEqualTo(60));
  });

  testWidgets('照護者模式達 Material 的 48dp 下限', (tester) async {
    await pumpButton(tester);

    final size = tester.getSize(find.byType(TextButton));
    expect(size.height, greaterThanOrEqualTo(48));
  });
}
