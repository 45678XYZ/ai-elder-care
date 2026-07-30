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
    testWidgets('兩格都沒填 → 兩個錯誤一次全講，各自長在自己那一格下面', (tester) async {
      await pump(tester, const SignInScreen());
      await tapPrimary(tester);

      expect(find.text('信箱格式錯誤'), findsOneWidget);
      expect(find.text('請填密碼'), findsOneWidget);
      expect(find.byType(FeedbackBanner), findsNothing);
    });

    testWidgets('信箱格式錯又沒填密碼 → 兩個錯誤一起出現', (tester) async {
      await pump(tester, const SignInScreen());
      await tester.enterText(find.byType(TextField).first, 'not-an-email');
      await tapPrimary(tester);

      expect(find.text('信箱格式錯誤'), findsOneWidget);
      expect(find.text('請填密碼'), findsOneWidget);
    });

    testWidgets('登入不驗密碼格式，只看有沒有填', (tester) async {
      // 既有帳號的密碼未必符合現行規則，在登入頁擋格式會把合法使用者關在門外。
      await pump(tester, const SignInScreen());
      await tester.enterText(find.byType(TextField).first, 'a@example.com');
      await tester.enterText(find.byType(TextField).last, 'abc');
      await tapPrimary(tester);

      expect(find.text('請填密碼'), findsNothing);
      expect(find.text('密碼格式錯誤'), findsNothing);
      // 有填就送去後端，由後端判定對不對
      expect(find.text('信箱或密碼錯誤'), findsOneWidget);
    });

    testWidgets('信箱格式不對會擋下來，不必等後端', (tester) async {
      await pump(tester, const SignInScreen());
      await tester.enterText(find.byType(TextField).first, 'not-an-email');
      await tester.enterText(find.byType(TextField).last, 'secret123');
      await tapPrimary(tester);

      expect(find.text('信箱格式錯誤'), findsOneWidget);
    });

    testWidgets('信箱的錯誤長在信箱欄位下方，不丟到頁尾', (tester) async {
      await pump(tester, const SignInScreen());
      await tester.enterText(find.byType(TextField).first, 'not-an-email');
      await tester.enterText(find.byType(TextField).last, 'secret123');
      await tapPrimary(tester);

      final emailField = tester.getRect(find.byType(TextField).first);
      final passwordField = tester.getRect(find.byType(TextField).last);
      final error = tester.getRect(find.text('信箱格式錯誤'));
      expect(error.top, greaterThanOrEqualTo(emailField.bottom),
          reason: '錯誤要在信箱欄位下方');
      expect(error.bottom, lessThanOrEqualTo(passwordField.top),
          reason: '錯誤要在密碼欄位之前，才指得到是信箱有問題');
      expect(find.byType(FeedbackBanner), findsNothing);
    });

    testWidgets('「信箱或密碼錯誤」指不到欄位，留在頁尾', (tester) async {
      // 這句刻意不說是哪一個錯（不洩漏信箱是否註冊過），所以不能掛在任一欄位下面
      await pump(tester, const SignInScreen());
      await tester.enterText(find.byType(TextField).first, 'a@example.com');
      await tester.enterText(find.byType(TextField).last, 'wrong123');
      await tapPrimary(tester);

      expect(find.byType(FeedbackBanner), findsOneWidget);
    });

    testWidgets('開始改信箱就把錯誤收掉', (tester) async {
      await pump(tester, const SignInScreen());
      await tester.enterText(find.byType(TextField).first, 'not-an-email');
      await tester.enterText(find.byType(TextField).last, 'secret123');
      await tapPrimary(tester);
      expect(find.text('信箱格式錯誤'), findsOneWidget);

      await tester.enterText(find.byType(TextField).first, 'a@example.com');
      await tester.pump();

      expect(find.text('信箱格式錯誤'), findsNothing);
    });

    testWidgets('帳號密碼錯會講，而且不說是哪一個錯', (tester) async {
      await pump(tester, const SignInScreen());
      await tester.enterText(find.byType(TextField).first, 'a@example.com');
      await tester.enterText(find.byType(TextField).last, 'wrong123');
      await tapPrimary(tester);

      expect(find.text('信箱或密碼錯誤'), findsOneWidget);
    });
  });

  group('註冊頁', () {
    testWidgets('三格都沒填 → 三個錯誤一次全講，各自長在自己那一格下面', (tester) async {
      // 逐項回報的話，三件事都有問題的人得送出三次才看得完。
      await pump(tester, const SignUpScreen(), size: const Size(390, 1400));
      await tapPrimary(tester);

      expect(find.text('信箱格式錯誤'), findsOneWidget);
      expect(find.text('密碼格式錯誤'), findsOneWidget);
      expect(find.text('請選擇身分'), findsOneWidget);
      // 沒填不另外給「請填…」，也不留任何頁尾的一句話
      expect(find.byType(FeedbackBanner), findsNothing);
    });

    testWidgets('有信箱但格式錯、密碼沒填 → 兩個錯誤一起出現', (tester) async {
      await pump(tester, const SignUpScreen(), size: const Size(390, 1400));
      await tester.enterText(find.byType(TextField).first, 'not-an-email');
      await tester.tap(find.text('長輩'));
      await tester.pump();
      await tapPrimary(tester);

      expect(find.text('信箱格式錯誤'), findsOneWidget);
      expect(find.text('密碼格式錯誤'), findsOneWidget);
      expect(find.byType(FeedbackBanner), findsNothing);
    });

    testWidgets('信箱格式的錯誤也長在信箱欄位下方', (tester) async {
      await pump(tester, const SignUpScreen(), size: const Size(390, 1400));
      await tester.enterText(find.byType(TextField).first, 'not-an-email');
      await tester.enterText(find.byType(TextField).last, 'secret123');
      await tapPrimary(tester);

      final emailField = tester.getRect(find.byType(TextField).first);
      final passwordField = tester.getRect(find.byType(TextField).last);
      final error = tester.getRect(find.text('信箱格式錯誤'));
      expect(error.top, greaterThanOrEqualTo(emailField.bottom));
      expect(error.bottom, lessThanOrEqualTo(passwordField.top));
      expect(find.byType(FeedbackBanner), findsNothing);
      // 信箱的說明也留著，跟密碼規則一樣不被錯誤蓋掉
      expect(find.text('等一下會寄驗證碼到這個信箱'), findsOneWidget);
    });

    testWidgets('密碼規則一進頁面就看得到，不是等送出才講', (tester) async {
      await pump(tester, const SignUpScreen());
      expect(find.text('至少 8 個字，要有英文字母和數字'), findsOneWidget);
    });

    testWidgets('密碼不合規則會擋，而且規則不會被錯誤蓋掉', (tester) async {
      await pump(tester, const SignUpScreen(), size: const Size(390, 1400));
      await tester.enterText(find.byType(TextField).first, 'a@example.com');
      await tester.enterText(find.byType(TextField).last, 'abc');
      await tapPrimary(tester);

      expect(find.text('密碼格式錯誤'), findsOneWidget);
      // 規則本身就是修正方法，出錯時更需要留著
      expect(find.text('至少 8 個字，要有英文字母和數字'), findsOneWidget);
    });

    testWidgets('密碼的錯誤長在密碼欄位下方，不丟到頁尾', (tester) async {
      await pump(tester, const SignUpScreen(), size: const Size(390, 1400));
      await tester.enterText(find.byType(TextField).first, 'a@example.com');
      await tester.enterText(find.byType(TextField).last, 'abc');
      await tapPrimary(tester);

      final field = tester.getRect(find.byType(TextField).last);
      final error = tester.getRect(find.text('密碼格式錯誤'));
      final rule = tester.getRect(find.text('至少 8 個字，要有英文字母和數字'));
      expect(error.top, greaterThanOrEqualTo(field.bottom),
          reason: '錯誤訊息要在密碼欄位下方');
      expect(error.top, lessThan(rule.top), reason: '錯誤在規則說明上方');
      // 頁尾 banner 是整頁層級錯誤用的；欄位層級的錯誤跑到那裡，人得自己回頭找欄位
      expect(find.byType(FeedbackBanner), findsNothing);
    });

    testWidgets('開始改密碼就把錯誤收掉', (tester) async {
      await pump(tester, const SignUpScreen(), size: const Size(390, 1400));
      await tester.enterText(find.byType(TextField).first, 'a@example.com');
      await tester.enterText(find.byType(TextField).last, 'abc');
      await tapPrimary(tester);
      expect(find.text('密碼格式錯誤'), findsOneWidget);

      await tester.enterText(find.byType(TextField).last, 'abc1');
      await tester.pump();

      expect(find.text('密碼格式錯誤'), findsNothing);
      expect(find.text('至少 8 個字，要有英文字母和數字'), findsOneWidget);
    });

    testWidgets('密碼可以按鈕顯示出來再收回去', (tester) async {
      await pump(tester, const SignUpScreen());
      final password = find.byType(TextField).last;
      expect(tester.widget<TextField>(password).obscureText, isTrue);

      await tester.tap(find.byTooltip('顯示密碼'));
      await tester.pumpAndSettle();
      expect(tester.widget<TextField>(password).obscureText, isFalse);

      await tester.tap(find.byTooltip('隱藏密碼'));
      await tester.pumpAndSettle();
      expect(tester.widget<TextField>(password).obscureText, isTrue);
    });

    testWidgets('登入頁的密碼不給顯示鈕（只有註冊頁要求）', (tester) async {
      await pump(tester, const SignInScreen());
      expect(find.byTooltip('顯示密碼'), findsNothing);
    });

    testWidgets('選了身分就把「請選擇身分」收掉，不必等下一次送出', (tester) async {
      await pump(tester, const SignUpScreen(), size: const Size(390, 1400));
      await tapPrimary(tester);
      expect(find.text('請選擇身分'), findsOneWidget);

      await tester.tap(find.text('長輩'));
      await tester.pump();

      expect(find.text('請選擇身分'), findsNothing);
    });

    testWidgets('兩個身分都沒有預設選取', (tester) async {
      // 預設任一邊，等於在使用者沒表態時默默替他指派身分。
      await pump(tester, const SignUpScreen());

      final cards =
          tester.widgetList<BigChoiceCard>(find.byType(BigChoiceCard));
      expect(cards.length, 2);
      expect(cards.every((c) => !c.selected), isTrue);
    });

    testWidgets('同意條款沒有預設勾選', (tester) async {
      // 理由同身分：不能在使用者沒表態時默默替他同意資料保留政策。
      await pump(tester, const SignUpScreen());

      final consent =
          tester.widget<ConsentCheckbox>(find.byType(ConsentCheckbox));
      expect(consent.checked, isFalse);
    });

    testWidgets('沒勾同意就按註冊 → 擋下來並說明原因', (tester) async {
      await pump(tester, const SignUpScreen(), size: const Size(390, 1400));
      await tester.enterText(find.byType(TextField).first, 'a@example.com');
      await tester.enterText(find.byType(TextField).last, 'secret123');
      await tester.tap(find.text('長輩'));
      await tester.pump();
      await tapPrimary(tester);

      expect(find.text('請先閱讀並同意使用者同意機制與資料保留政策'), findsOneWidget);
      // 其他三格都填對了，不該連坐冒出別的錯
      expect(find.text('信箱格式錯誤'), findsNothing);
      expect(find.text('密碼格式錯誤'), findsNothing);
      expect(find.text('請選擇身分'), findsNothing);
      expect(find.byType(FeedbackBanner), findsNothing);
    });

    testWidgets('勾了同意就把錯誤收掉，不必等下一次送出', (tester) async {
      await pump(tester, const SignUpScreen(), size: const Size(390, 1400));
      await tapPrimary(tester);
      expect(find.text('請先閱讀並同意使用者同意機制與資料保留政策'), findsOneWidget);

      await tester.tap(find.byType(ConsentCheckbox));
      await tester.pump();

      expect(find.text('請先閱讀並同意使用者同意機制與資料保留政策'), findsNothing);
    });

    testWidgets('看得到政策說明的入口，不是只有一個沒展開的詞條', (tester) async {
      await pump(tester, const SignUpScreen(), size: const Size(390, 1400));
      expect(find.text('查看使用者同意機制與資料保留政策說明'), findsOneWidget);
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
          GoRoute(
              path: '/auth/sign-up', builder: (_, __) => const SignUpScreen()),
          GoRoute(
            path: '/setup',
            builder: (_, state) => SetupScreen(email: state.extra as String?),
          ),
          GoRoute(
            path: '/auth/verify',
            builder: (_, state) =>
                VerifyEmailScreen(email: state.extra as String? ?? ''),
          ),
          GoRoute(
              path: '/auth/sign-in', builder: (_, __) => const SignInScreen()),
        ],
      );
      addTearDown(router.dispose);

      await tester.pumpWidget(
        MaterialApp.router(theme: buildAppTheme(), routerConfig: router),
      );
      await tester.pumpAndSettle();
    }

    /// 填完信箱、密碼並勾選同意條款——除了身分之外都完成的狀態。
    /// 身分刻意留給各測試自己決定（有些測試就是要驗「沒選身分」）。
    Future<void> fillForm(WidgetTester tester) async {
      await tester.enterText(find.byType(TextField).first, 'a@example.com');
      await tester.enterText(find.byType(TextField).last, 'secret123');
      await tester.tap(find.byType(ConsentCheckbox));
      await tester.pump();
    }

    testWidgets('沒選身分就按註冊 → 說明原因，而且不會進驗證碼頁', (tester) async {
      await pumpWithRouter(tester);
      await fillForm(tester);
      await tapPrimary(tester);

      expect(find.text('請選擇身分'), findsOneWidget);
      // 訊息要在身分卡下面、註冊鈕上面，才指得到是哪一段沒完成
      final cards = tester.getRect(find.byType(BigChoiceCard).last);
      final button = tester.getRect(find.byType(BigButton));
      final error = tester.getRect(find.text('請選擇身分'));
      expect(error.top, greaterThanOrEqualTo(cards.bottom));
      expect(error.bottom, lessThanOrEqualTo(button.top));
      expect(find.byType(FeedbackBanner), findsNothing);
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

      expect(find.text('驗證碼錯誤，請再確認一次'), findsOneWidget);
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
