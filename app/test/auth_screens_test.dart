import 'package:ai_elder_care/caregiver/screens/setup_screen.dart';
import 'package:ai_elder_care/shared/screens/sign_in_screen.dart';
import 'package:ai_elder_care/shared/screens/sign_up_screen.dart';
import 'package:ai_elder_care/shared/screens/verify_email_screen.dart';
import 'package:ai_elder_care/shared/services/auth_service.dart';
import 'package:ai_elder_care/shared/services/demo_auth_backend.dart';
import 'package:ai_elder_care/shared/widgets/form_widgets.dart';
import 'package:ai_elder_care/theme/app_theme.dart';
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:go_router/go_router.dart';
import 'package:shared_preferences/shared_preferences.dart';

/// 登入／註冊／驗證碼三頁。
///
/// 重點在**送出前的把關與回饋**：這三頁的每一種失敗如果只是靜靜地什麼都不做，
/// 使用者（尤其長輩）會以為 App 壞了。導航後的結果由 router 負責，不在這裡測。
void main() {
  setUp(() {
    // 每個測試給乾淨的假後端，且不要人為延遲。
    AuthService.instance.backend = DemoAuthBackend(latency: Duration.zero);
    // 註冊頁會把宣告的身分寫進 SharedPreferences，每個測試都要從空的開始。
    SharedPreferences.setMockInitialValues({});
  });

  Future<void> pump(
    WidgetTester tester,
    Widget screen, {
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
          child: screen,
        ),
      ),
    );
    await tester.pumpAndSettle();
  }

  /// 送出後的等待。
  ///
  /// 不能用 pumpAndSettle：按鈕忙碌時會顯示 CircularProgressIndicator，
  /// 那是永不停止的動畫，pumpAndSettle 會一路等到測試逾時。改推進固定的幾個
  /// frame，足夠讓假後端的 Future 完成並重畫。
  Future<void> settle(WidgetTester tester) async {
    for (var i = 0; i < 5; i++) {
      await tester.pump(const Duration(milliseconds: 50));
    }
  }

  /// 主要動作按鈕。用型別而不是文字定位——「登入」「註冊」同時是標題與按鈕，
  /// 用文字找會同時命中兩個。
  Future<void> tapPrimary(WidgetTester tester) async {
    await tester.tap(find.byType(BigButton));
    await settle(tester);
  }

  final screens = <String, Widget Function()>{
    '登入': () => const SignInScreen(),
    '註冊': () => const SignUpScreen(),
    '驗證碼': () => const VerifyEmailScreen(email: 'a@example.com'),
  };

  group('畫得出來', () {
    for (final entry in screens.entries) {
      testWidgets(entry.key, (tester) async {
        await pump(tester, entry.value());
        expect(tester.takeException(), isNull);
      });
    }
  });

  group('兩倍字級不 overflow', () {
    for (final entry in screens.entries) {
      testWidgets(entry.key, (tester) async {
        await pump(tester, entry.value(), textScale: 2.0);
        expect(tester.takeException(), isNull);
      });
    }
  });

  group('登入頁', () {
    testWidgets('空白送出會說要填', (tester) async {
      await pump(tester, const SignInScreen());
      await tapPrimary(tester);

      expect(find.text('請填信箱和密碼'), findsOneWidget);
    });

    testWidgets('信箱格式不對會擋下來，不必等後端', (tester) async {
      await pump(tester, const SignInScreen());
      await tester.enterText(find.byType(TextField).first, 'not-an-email');
      await tester.enterText(find.byType(TextField).last, 'secret123');
      await tapPrimary(tester);

      expect(find.text('信箱格式不太對，請再看一下'), findsOneWidget);
    });

    testWidgets('帳號密碼錯會講，而且不說是哪一個錯', (tester) async {
      await pump(tester, const SignInScreen());
      await tester.enterText(find.byType(TextField).first, 'a@example.com');
      await tester.enterText(find.byType(TextField).last, 'wrong123');
      await tapPrimary(tester);

      expect(find.text('信箱或密碼不對'), findsOneWidget);
    });
  });

  group('註冊頁', () {
    testWidgets('空白送出會說要填', (tester) async {
      await pump(tester, const SignUpScreen());
      await tapPrimary(tester);

      expect(find.text('請填信箱和密碼'), findsOneWidget);
    });

    testWidgets('密碼規則寫在畫面上，不是等送出才講', (tester) async {
      await pump(tester, const SignUpScreen());
      expect(find.text('至少 8 個字，要有英文字母和數字'), findsOneWidget);
    });

    testWidgets('密碼不合規則會擋，訊息說得出規則', (tester) async {
      await pump(tester, const SignUpScreen());
      await tester.enterText(find.byType(TextField).first, 'a@example.com');
      await tester.enterText(find.byType(TextField).last, 'abc');
      await tapPrimary(tester);

      expect(find.text('密碼至少 8 個字，要有英文字母和數字'), findsOneWidget);
    });

    testWidgets('兩個身分都沒有預設選取', (tester) async {
      // 預設任一邊，等於在使用者沒表態時默默替他指派身分。
      await pump(tester, const SignUpScreen());

      final cards = tester.widgetList<BigChoiceCard>(find.byType(BigChoiceCard));
      expect(cards.length, 2);
      expect(cards.every((c) => !c.selected), isTrue);
    });

    /// 註冊頁 + 註冊流程下一站的最小路由。
    ///
    /// 「有沒有進到下一頁」不能只看畫面上有沒有錯誤訊息——註冊頁是用 `context.push`
    /// 導航的，沒有 router 就根本 push 不了，等於測不到那條路。
    ///
    /// 要收 `/setup`：長輩的下一站是先建基本資料，不是驗證碼（見 SignUpScreen）。
    /// 兩條路徑的完整銜接在 first_run_flow_test.dart，這裡只看註冊頁把人送去哪裡。
    Future<void> pumpWithRouter(WidgetTester tester) async {
      tester.view
        ..physicalSize = const Size(390, 3000)
        ..devicePixelRatio = 1.0;
      addTearDown(tester.view.reset);

      final router = GoRouter(
        initialLocation: '/auth/sign-up',
        routes: [
          GoRoute(path: '/auth/sign-up', builder: (_, __) => const SignUpScreen()),
          GoRoute(
            path: '/setup',
            builder: (_, state) => SetupScreen(email: state.extra as String?),
          ),
          GoRoute(
            path: '/auth/verify',
            builder: (_, state) =>
                VerifyEmailScreen(email: state.extra as String? ?? ''),
          ),
          GoRoute(path: '/auth/sign-in', builder: (_, __) => const SignInScreen()),
        ],
      );
      addTearDown(router.dispose);

      await tester.pumpWidget(
        MaterialApp.router(theme: buildAppTheme(), routerConfig: router),
      );
      await tester.pumpAndSettle();
    }

    Future<void> fillForm(WidgetTester tester) async {
      await tester.enterText(find.byType(TextField).first, 'a@example.com');
      await tester.enterText(find.byType(TextField).last, 'secret123');
      await tester.pump();
    }

    testWidgets('沒選身分就按註冊 → 說明原因，而且不會進驗證碼頁', (tester) async {
      await pumpWithRouter(tester);
      await fillForm(tester);
      await tapPrimary(tester);

      expect(find.text('請先選擇你是長輩還是家人'), findsOneWidget);
      expect(find.byType(VerifyEmailScreen), findsNothing);
      // 也不該偷偷把帳號建起來
      final prefs = await SharedPreferences.getInstance();
      expect(prefs.getString('auth_pending_role_a@example.com'), isNull);
    });

    testWidgets('選長輩 → 先去建基本資料，並把身分暫存起來', (tester) async {
      await pumpWithRouter(tester);
      await fillForm(tester);
      await tester.tap(find.text('長輩'));
      await tester.pump();
      await tapPrimary(tester);

      // 長輩的基本資料在註冊流程裡就填完，不是等第一次登入之後才補。
      expect(find.byType(SetupScreen), findsOneWidget);
      expect(find.byType(VerifyEmailScreen), findsNothing);
      final prefs = await SharedPreferences.getInstance();
      expect(prefs.getString('auth_pending_role_a@example.com'), 'elder');
    });

    testWidgets('選家人 → 直接進驗證碼頁（照護者沒有要填的資料）', (tester) async {
      await pumpWithRouter(tester);
      await fillForm(tester);
      await tester.tap(find.text('家人 / 照護者'));
      await tester.pump();
      await tapPrimary(tester);

      expect(find.byType(VerifyEmailScreen), findsOneWidget);
      expect(find.byType(SetupScreen), findsNothing);
      final prefs = await SharedPreferences.getInstance();
      expect(prefs.getString('auth_pending_role_a@example.com'), 'caregiver');
    });
  });

  group('驗證碼頁', () {
    testWidgets('信箱顯示在畫面上，讓人確認寄到哪裡', (tester) async {
      await pump(tester, const VerifyEmailScreen(email: 'grandma@example.com'));
      expect(find.textContaining('grandma@example.com'), findsOneWidget);
    });

    testWidgets('位數不足會說要填幾位', (tester) async {
      await pump(tester, const VerifyEmailScreen(email: 'a@example.com'));
      await tester.enterText(find.byType(TextField), '123');
      await tapPrimary(tester);

      expect(find.text('請輸入信件裡的 6 位數字'), findsOneWidget);
    });

    testWidgets('只收得下六位數字', (tester) async {
      await pump(tester, const VerifyEmailScreen(email: 'a@example.com'));
      final field = find.byType(TextField);
      await tester.enterText(field, '12345678');
      await tester.pumpAndSettle();

      expect(tester.widget<TextField>(field).controller?.text, '123456');
    });

    testWidgets('驗證碼錯會講清楚', (tester) async {
      // 先註冊，帳號存在但碼是錯的
      await AuthService.instance.backend
          .signUp(email: 'a@example.com', password: 'secret123');
      await pump(tester, const VerifyEmailScreen(email: 'a@example.com'));
      await tester.enterText(find.byType(TextField), '000000');
      await tapPrimary(tester);

      expect(find.text('驗證碼不對，請再確認一次'), findsOneWidget);
    });

    testWidgets('重寄太快會被擋，並說明原因', (tester) async {
      await AuthService.instance.backend
          .signUp(email: 'a@example.com', password: 'secret123');
      await pump(tester, const VerifyEmailScreen(email: 'a@example.com'));
      await tester.tap(find.byType(TextLink));
      await settle(tester);

      expect(find.text('太頻繁了，請等一下再試'), findsOneWidget);
    });
  });
}
