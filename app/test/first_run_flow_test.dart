import 'package:e_hakka_care/app_router.dart';
import 'package:e_hakka_care/caregiver/screens/setup_screen.dart';
import 'package:e_hakka_care/caregiver/screens/summaries_screen.dart';
import 'package:e_hakka_care/elder/screens/today_screen.dart';
import 'package:e_hakka_care/main.dart';
import 'package:e_hakka_care/shared/screens/sign_in_screen.dart';
import 'package:e_hakka_care/shared/screens/sign_up_screen.dart';
import 'package:e_hakka_care/shared/screens/verify_email_screen.dart';
import 'package:e_hakka_care/shared/services/auth_service.dart';
import 'package:e_hakka_care/shared/services/demo_auth_backend.dart';
import 'package:e_hakka_care/shared/services/session_store.dart';
import 'package:e_hakka_care/shared/widgets/form_widgets.dart';
import 'package:e_hakka_care/theme/app_theme.dart';
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:shared_preferences/shared_preferences.dart';

/// 第一次使用的完整路徑。
///
/// 長者：註冊（選長輩）→ **建立長輩的基本資料** → 驗證碼 → 登入 → 今日頁。
/// 照護者：註冊（選家人）→ 驗證碼 → 登入 → 摘要頁（沒有要填的資料）。
///
/// 為什麼長者的初次設定放在註冊流程**之內**：對使用者來說「開帳號」是一件事，
/// 中間被登入切成兩半（登入完才被要求設定）只會讓人以為註冊沒做完。代價是那個時間點
/// 還沒有帳號，資料只能先按 email 暫存，第一次登入才兌現——這段時序是 AppSession
/// 的 `setup_pending_` 與 router 對 /setup 的例外放行在處理的事。
///
/// 為什麼要從 App 根部（[AiElderCareApp]）跑而不是各畫面單獨測：落點是 router 的
/// redirect 決定的，而它同時看三件事（有沒有登入、有沒有身分、長者有沒有建資料）。
/// 個別畫面的測試通過，不代表這三個條件銜接起來會把人帶到對的地方。
void main() {
  /// 每個測試的起點：清空持久化狀態並把兩個單例的記憶體欄位一起歸零。
  ///
  /// [AuthService] 與 [AppSession] 都是單例，欄位會跨測試留在記憶體裡；
  /// 只清 SharedPreferences 不夠，一定要重新 restore／load 才算「全新狀態」。
  Future<DemoAuthBackend> resetToFreshDevice(
      [Map<String, Object> prefs = const {}]) async {
    TestWidgetsFlutterBinding.ensureInitialized();
    SharedPreferences.setMockInitialValues(prefs);
    // 假後端不要人為延遲，讓測試只需推進少數幾個 frame。
    final backend = DemoAuthBackend(latency: Duration.zero);
    AuthService.instance.backend = backend;
    await AuthService.instance.restore();
    await AppSession.instance
        .loadForAccount(AuthService.instance.identity?.userId);
    AppSession.instance.elders = const [];
    return backend;
  }

  setUp(resetToFreshDevice);

  /// 送出後的等待。
  ///
  /// 不能用 pumpAndSettle：按鈕忙碌時的 CircularProgressIndicator 是永不停止的動畫，
  /// pumpAndSettle 會一路等到測試逾時。改推進固定的幾個 frame，
  /// 足夠讓假後端的 Future 完成、redirect 重算並重畫。寫法同 auth_screens_test.dart，
  /// 只是 frame 數放寬——這裡是整個 App，換頁的轉場動畫（約 300ms）也要推完，
  /// 否則新頁還在滑入、舊頁還沒退場，點按會落在轉場中的圖層上而打不到按鈕。
  ///
  /// 幀數從 20 加到 40（1 秒 → 2 秒）是因為第一次登入時 `consumePendingSetup` 會
  /// `await` 一次 `POST /elders`（長者自註冊綁定 sub→elder_id），demo 那條是兩段
  /// 各 400ms 的 `Future.delayed` 串起來。推不完的話測試結束時計時器還掛著，
  /// 會炸「A Timer is still pending even after the widget tree was disposed」——
  /// 而那個訊息完全看不出來是這條路造成的。
  Future<void> settle(WidgetTester tester) async {
    for (var i = 0; i < 40; i++) {
      await tester.pump(const Duration(milliseconds: 50));
    }
  }

  /// 視窗給得很高（3000）是必要的：註冊頁與初次設定頁都比手機一屏長，
  /// 預設 800×600 之下按鈕根本沒被 build，tap 會找不到目標。
  void useTallView(WidgetTester tester) {
    tester.view
      ..physicalSize = const Size(390, 3000)
      ..devicePixelRatio = 1.0;
    addTearDown(tester.view.reset);
  }

  /// 掛上整個 App（起點同正式啟動：未登入落在登入頁）。
  Future<void> pumpApp(WidgetTester tester) async {
    useTallView(tester);
    await tester.pumpWidget(AiElderCareApp());
    await settle(tester);
  }

  /// 直接從某個位置起跑，用來驗證守衛（web 上直接開網址／重整就是這個情形）。
  Future<void> pumpAppAt(WidgetTester tester, String location) async {
    useTallView(tester);
    final router = buildRouter(initialLocation: location);
    addTearDown(router.dispose);
    await tester.pumpWidget(
      MaterialApp.router(theme: buildAppTheme(), routerConfig: router),
    );
    await settle(tester);
  }

  // 以下的 finder 一律限定在「現在這一頁」之內。
  // push 進來的新頁之下，舊頁的 widget 仍留在 tree 裡（Navigator 不會丟掉它），
  // 全域找 TextField／BigButton 會同時命中上一頁的欄位與按鈕。
  Finder inScreen(Type screen, Finder matching) =>
      find.descendant(of: find.byType(screen), matching: matching);

  Future<void> tapPrimary(WidgetTester tester, Type screen) async {
    await tester.tap(inScreen(screen, find.byType(BigButton)));
    await settle(tester);
  }

  /// 填「信箱 + 密碼」兩欄（登入頁與註冊頁的欄位順序一致）。
  Future<void> fillCredentials(
    WidgetTester tester,
    Type screen, {
    String email = 'grandma@example.com',
  }) async {
    final fields = inScreen(screen, find.byType(TextField));
    await tester.enterText(fields.first, email);
    await tester.enterText(fields.last, 'secret123');
    await tester.pump();
  }

  /// 從登入頁走到註冊頁，填帳密、選身分、送出。
  Future<void> submitSignUp(
    WidgetTester tester, {
    required String roleLabel,
    String email = 'grandma@example.com',
  }) async {
    expect(find.byType(SignInScreen), findsOneWidget);
    await tester.tap(find.text('還沒有帳號？註冊'));
    await settle(tester);
    expect(find.byType(SignUpScreen), findsOneWidget);

    await fillCredentials(tester, SignUpScreen, email: email);
    await tester.tap(inScreen(SignUpScreen, find.text(roleLabel)));
    await tester.pump();
    // 同意條款是註冊的必要條件，沒勾就會被擋在註冊頁
    await tester.tap(inScreen(SignUpScreen, find.byType(ConsentCheckbox)));
    await tester.pump();
    await tapPrimary(tester, SignUpScreen);
  }

  /// 在初次設定頁填完必填欄位並送出。
  ///
  /// 三個必填：姓名、出生年、居住地區（稱呼是選填）。欄位順序即畫面順序，
  /// 靠索引取用——加欄位時這裡要跟著改，那是刻意的：新增必填欄位卻沒有任何
  /// 測試察覺，等於流程被擋住也沒人知道。
  Future<void> completeSetup(WidgetTester tester, String name) async {
    final fields = inScreen(SetupScreen, find.byType(TextField));
    await tester.enterText(fields.at(0), name); // 姓名
    await tester.enterText(fields.at(2), '1948'); // 出生年
    await tester.enterText(fields.at(3), '台北市大安區'); // 居住地區
    await tester.pump();
    await tester.tap(find.text('完成設定'));
    await settle(tester);
  }

  /// 輸入驗證碼並送出（送出後回到登入頁）。
  Future<void> submitVerifyCode(WidgetTester tester) async {
    expect(find.byType(VerifyEmailScreen), findsOneWidget);
    await tester.enterText(inScreen(VerifyEmailScreen, find.byType(TextField)),
        DemoAuthBackend.demoCode);
    await tapPrimary(tester, VerifyEmailScreen);
  }

  /// 用同一組帳密登入，停在「登入成功後 router 決定的落點」。
  Future<void> signIn(
    WidgetTester tester, {
    String email = 'grandma@example.com',
  }) async {
    expect(find.byType(SignInScreen), findsOneWidget);
    await fillCredentials(tester, SignInScreen, email: email);
    await tapPrimary(tester, SignInScreen);
  }

  testWidgets('註冊選長輩 → 當場建資料 → 驗證碼 → 登入後直接進今日頁', (tester) async {
    await pumpApp(tester);
    await submitSignUp(tester, roleLabel: '長輩');

    // 送出註冊的下一頁就是建資料，而且此時還沒登入、還沒到驗證碼。
    expect(find.byType(SetupScreen), findsOneWidget);
    expect(find.text('建立長輩的基本資料'), findsOneWidget);
    expect(find.byType(VerifyEmailScreen), findsNothing);
    expect(AuthService.instance.isSignedIn, isFalse);

    // 資料填完才進驗證碼。
    await completeSetup(tester, '陳阿蘭');
    expect(find.byType(VerifyEmailScreen), findsOneWidget);

    await submitVerifyCode(tester);
    await signIn(tester);

    // 註冊時填的資料已在第一次登入時兌現到這個帳號，所以不再出現第二次初次設定。
    expect(find.byType(TodayScreen), findsOneWidget);
    expect(find.byType(SetupScreen), findsNothing);
    expect(AppSession.instance.displayName, '陳阿蘭');
  });

  testWidgets('註冊選家人 → 流程不變，不會被要求建長輩資料', (tester) async {
    await pumpApp(tester);
    await submitSignUp(tester, roleLabel: '家人 / 照護者', email: 'son@example.com');

    // 照護者沒有要填的資料，送出後直接是驗證碼。
    expect(find.byType(VerifyEmailScreen), findsOneWidget);
    expect(find.byType(SetupScreen), findsNothing);

    await submitVerifyCode(tester);
    await signIn(tester, email: 'son@example.com');

    expect(find.byType(SummariesScreen), findsOneWidget);
    expect(find.byType(SetupScreen), findsNothing);
  });

  testWidgets('已登入的長者但本機沒有這個帳號的資料 → 仍然要先建資料（換裝置的退路）', (tester) async {
    // 順便釘住舊的裝置層級旗標（`setup_done`）不再有任何效力：那個 true 是誰按出來的
    // 已無從得知，猜錯的代價是讓長者跳過建資料，之後每一頁都沒有稱呼與行程可用。
    final backend = await resetToFreshDevice(const {'setup_done': true});

    // 模擬「在別台裝置註冊、這台只是登入」：帳號存在且已驗證，但本機沒有任何暫存資料。
    const email = 'grandma@example.com';
    await backend.signUp(email: email, password: 'secret123');
    await backend.confirmSignUp(email: email, code: DemoAuthBackend.demoCode);
    backend.markAsElder(email: email, elderId: 'eld_0123456789ab');
    final identity =
        await AuthService.instance.signIn(email: email, password: 'secret123');
    await AppSession.instance.loadForAccount(identity.userId);

    await pumpApp(tester);

    expect(find.byType(SetupScreen), findsOneWidget);
    expect(find.byType(TodayScreen), findsNothing);
  });

  testWidgets('未登入又沒有信箱時直接開 /setup → 被導回登入頁', (tester) async {
    // 註冊流程之外的 /setup 沒有東西可以掛：資料要按 email 暫存，而 email 只有註冊頁
    // 用 extra 帶進來時才有。做法與 /auth/verify 一致（沒有 extra 就退回）。
    await pumpAppAt(tester, '/setup');

    expect(find.byType(SignInScreen), findsOneWidget);
    expect(find.byType(SetupScreen), findsNothing);
  });
}
