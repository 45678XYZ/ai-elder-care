import 'package:e_hakka_care/caregiver/screens/setup_screen.dart';
import 'package:e_hakka_care/shared/models/elder.dart';
import 'package:e_hakka_care/shared/services/session_store.dart';
import 'package:e_hakka_care/theme/app_theme.dart';
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:go_router/go_router.dart';
import 'package:shared_preferences/shared_preferences.dart';

/// 初次設定的必填欄位。
///
/// 出生年與居住地區原本**全 App 沒有任何地方填得到**——`birth_year` 只被管理頁讀來
/// 算年齡，`address_region` 連讀都沒有人讀。兩個都是後端要用的：對話大腦查天氣
/// （`get_weather_forecast`）靠地區，沒有它答不出「明天會不會下雨」。
///
/// 這裡釘的是「填了要存得住」與「沒填要擋下來」，因為這條路上有兩種儲存路徑
/// （已登入走 saveSetup、註冊流程走 savePendingSetup），只驗畫面會漏掉另一條。
void main() {
  const sub = 'sub-caregiver';

  setUp(() async {
    SharedPreferences.setMockInitialValues({});
    await AppSession.instance.loadForAccount(sub);
  });

  /// 送出成功後畫面會導航（已登入 `go('/')`、註冊流程 `push('/auth/verify')`），
  /// 所以要給一個真的 router——沒有的話 submit 會在存完之後才丟
  /// 「No GoRouter found in context」，測試看起來像儲存失敗，其實不是。
  Future<void> pumpSetup(WidgetTester tester, {String? email}) async {
    tester.view
      ..physicalSize = const Size(390, 3000)
      ..devicePixelRatio = 1.0;
    addTearDown(tester.view.reset);

    final router = GoRouter(
      initialLocation: '/setup',
      routes: [
        GoRoute(path: '/setup', builder: (_, __) => SetupScreen(email: email)),
        GoRoute(path: '/', builder: (_, __) => const _Stub('home')),
        GoRoute(
            path: '/auth/verify', builder: (_, __) => const _Stub('verify')),
      ],
    );
    addTearDown(router.dispose);

    await tester.pumpWidget(
        MaterialApp.router(theme: buildAppTheme(), routerConfig: router));
    await tester.pumpAndSettle();
  }

  /// 按下「完成設定」並等到真的做完。
  ///
  /// **不能用 `pumpAndSettle`**：送出期間按鈕會顯示 `CircularProgressIndicator`，
  /// 那是永不停止的動畫，settle 條件（沒有排程中的幀）永遠不成立，會一路等到
  /// 10 分鐘逾時。單獨跑這個檔可能僥倖過關，整套一起跑 CPU 被瓜分就必掛。
  /// 同樣的坑 `first_run_flow_test` 的 `settle()` 也踩過，那裡的註解寫得更詳細。
  ///
  /// 幀數要推到足以蓋過儲存之後那一次 `POST /elders`（長者自註冊時綁定
  /// sub→elder_id）：demo 那條路是兩段各 400ms 的 `Future.delayed` 串起來。
  /// 推不完的話測試結束時計時器還掛著 → 「A Timer is still pending even after
  /// the widget tree was disposed」。
  Future<void> submit(WidgetTester tester) async {
    await tester.tap(find.text('完成設定'));
    for (var i = 0; i < 40; i++) {
      await tester.pump(const Duration(milliseconds: 50));
    }
  }

  /// 欄位順序即畫面順序：姓名、稱呼、出生年、居住地區。
  Future<void> fill(
    WidgetTester tester, {
    String name = '陳阿蘭',
    String birthYear = '1948',
    String region = '台北市大安區',
  }) async {
    final fields = find.byType(TextField);
    await tester.enterText(fields.at(0), name);
    await tester.enterText(fields.at(2), birthYear);
    await tester.enterText(fields.at(3), region);
    await tester.pump();
  }

  testWidgets('兩個欄位都在畫面上', (tester) async {
    await pumpSetup(tester);
    expect(find.text('出生年（西元）'), findsOneWidget);
    expect(find.text('居住地區'), findsOneWidget);
  });

  testWidgets('填完送出，兩個值都存得住', (tester) async {
    await pumpSetup(tester);
    await fill(tester);
    await submit(tester);

    expect(AppSession.instance.elderBirthYear, 1948);
    expect(AppSession.instance.elderAddressRegion, '台北市大安區');
  });

  testWidgets('存進去的值重新登入後還在', (tester) async {
    await pumpSetup(tester);
    await fill(tester);
    await submit(tester);

    await AppSession.instance.clearForAccount(sub);
    expect(AppSession.instance.elderBirthYear, isNull, reason: '登出要先歸零');

    await AppSession.instance.loadForAccount(sub);
    expect(AppSession.instance.elderBirthYear, 1948);
    expect(AppSession.instance.elderAddressRegion, '台北市大安區');
  });

  testWidgets('出生年沒填會被擋下來', (tester) async {
    await pumpSetup(tester);
    await fill(tester, birthYear: '');
    await submit(tester);

    expect(find.text('請填出生年'), findsOneWidget);
    expect(AppSession.instance.setupDone, isFalse, reason: '沒過驗證不該算完成設定');
  });

  testWidgets('居住地區沒填會被擋下來', (tester) async {
    await pumpSetup(tester);
    await fill(tester, region: '');
    await submit(tester);

    expect(find.text('請填居住地區'), findsOneWidget);
    expect(AppSession.instance.setupDone, isFalse);
  });

  testWidgets('民國年會被擋下來——最容易打錯的那一種', (tester) async {
    await pumpSetup(tester);
    await fill(tester, birthYear: '37'); // 民國 37 年 = 西元 1948
    await submit(tester);

    expect(find.textContaining('1900'), findsOneWidget);
    expect(AppSession.instance.setupDone, isFalse);
  });

  testWidgets('不是數字會被擋下來', (tester) async {
    await pumpSetup(tester);
    await fill(tester, birthYear: '民國37年');
    await submit(tester);

    expect(find.textContaining('西元年份'), findsOneWidget);
  });

  testWidgets('選華語時看不到腔調，選客語才出現', (tester) async {
    await pumpSetup(tester);
    expect(find.text('客語腔調'), findsNothing, reason: '華語家庭不該被迫滑過六個用不到的選項');

    await tester.tap(find.text('客語'));
    await tester.pumpAndSettle();
    expect(find.text('客語腔調'), findsOneWidget);
    // 六腔都要在：每一腔各有獨立的 ASR/TTS 模型端點，少列一個等於那一腔的
    // 長輩永遠被當成四縣腔辨識。
    for (final d in HakkaDialect.values) {
      expect(find.text(d.label), findsOneWidget);
    }
  });

  testWidgets('選了腔調會存下來', (tester) async {
    await pumpSetup(tester);
    await fill(tester);
    await tester.tap(find.text('客語'));
    await tester.pumpAndSettle();
    await tester.tap(find.text('海陸'));
    await tester.pumpAndSettle();
    await submit(tester);

    expect(AppSession.instance.elderHakkaDialect, 'htia_hailu');
  });

  testWidgets('沒選過就是四縣——與後端預設一致', (tester) async {
    await pumpSetup(tester);
    await fill(tester);
    await submit(tester);

    expect(AppSession.instance.elderHakkaDialect, HakkaDialect.defaultValue);
  });

  testWidgets('註冊流程那條路也存得住——走的是另一個儲存函式', (tester) async {
    // email 非 null = 還沒登入，資料寄放在信箱底下（savePendingSetup），
    // 第一次登入才由 consumePendingSetup 兌現到帳號。
    await pumpSetup(tester, email: 'grandma@example.com');

    await fill(tester, birthYear: '1950', region: '新竹縣竹東鎮');
    await submit(tester);

    // runAsync：這一行不是靠 pump 驅動的，而 consumePendingSetup 裡面有
    // `Future.delayed`（兌現時會呼叫 `POST /elders`，demo 是 400ms 延遲）。
    // testWidgets 預設跑在 fake async 時鐘下，不 pump 就不前進——直接 await
    // 會永遠等不到，最後以 10 分鐘逾時收場。runAsync 讓它走真實時鐘。
    await tester.runAsync(() => AppSession.instance
        .consumePendingSetup(email: 'grandma@example.com', accountId: sub));

    expect(AppSession.instance.elderBirthYear, 1950);
    expect(AppSession.instance.elderAddressRegion, '新竹縣竹東鎮');
  });
}

/// 導航目的地的替身：這裡只在乎資料存不存得住，不驗落點長什麼樣。
class _Stub extends StatelessWidget {
  const _Stub(this.label);

  final String label;

  @override
  Widget build(BuildContext context) => Scaffold(body: Text(label));
}
