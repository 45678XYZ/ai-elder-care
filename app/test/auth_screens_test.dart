import 'package:ai_elder_care/shared/screens/sign_in_screen.dart';
import 'package:ai_elder_care/shared/screens/sign_up_screen.dart';
import 'package:ai_elder_care/shared/screens/verify_email_screen.dart';
import 'package:ai_elder_care/shared/services/auth_service.dart';
import 'package:ai_elder_care/shared/services/demo_auth_backend.dart';
import 'package:ai_elder_care/shared/widgets/form_widgets.dart';
import 'package:ai_elder_care/theme/app_theme.dart';
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';

/// 登入／註冊／驗證碼三頁。
///
/// 重點在**送出前的把關與回饋**：這三頁的每一種失敗如果只是靜靜地什麼都不做，
/// 使用者（尤其長輩）會以為 App 壞了。導航後的結果由 router 負責，不在這裡測。
void main() {
  setUp(() {
    // 每個測試給乾淨的假後端，且不要人為延遲。
    AuthService.instance.backend = DemoAuthBackend(latency: Duration.zero);
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
