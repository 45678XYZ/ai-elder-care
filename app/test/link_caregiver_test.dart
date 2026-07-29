import 'package:ai_elder_care/caregiver/screens/elders_screen.dart';
import 'package:ai_elder_care/elder/screens/link_caregiver_screen.dart';
import 'package:ai_elder_care/shared/services/demo_data.dart';
import 'package:ai_elder_care/shared/services/session_store.dart';
import 'package:ai_elder_care/theme/app_theme.dart';
import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
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

  // 綁定的另一半（api.md「綁定照護者」步驟 1）：照護者得先在自己的 App 看到 ID，
  // 才有東西可以報給家人。少了這個入口，上面那些測試涵蓋的流程在真實情境下無從開始。
  group('我的 ID（照護者端）', () {
    /// 掛上管理頁並等 DemoData 的延遲跑完。
    ///
    /// 刻意不用 `pumpAndSettle`：面板的 loading 轉圈會一直排新的一幀，
    /// settle 永遠不會發生。改成推固定的時間。
    Future<void> pumpManage(WidgetTester tester) async {
      tester.view
        ..physicalSize = const Size(390, 3000)
        ..devicePixelRatio = 1.0;
      addTearDown(tester.view.reset);

      await tester.pumpWidget(const MaterialApp(
        home: MediaQuery(
          data: MediaQueryData(size: Size(390, 3000), disableAnimations: true),
          child: EldersScreen(),
        ),
      ));
      await tester.pump(const Duration(seconds: 1));
      await tester.pump(const Duration(seconds: 1));
    }

    testWidgets('管理頁看得到 ID 入口，點開有 ID 可以複製', (tester) async {
      // ID 由帳號的 Cognito `sub` 衍生，所以要先有登入過的帳號。
      await AppSession.instance.loadForAccount('demo-sub-1');

      final clipboard = <MethodCall>[];
      tester.binding.defaultBinaryMessenger.setMockMethodCallHandler(
        SystemChannels.platform,
        (call) async {
          clipboard.add(call);
          return null;
        },
      );

      await pumpManage(tester);
      expect(find.text('ID'), findsOneWidget);

      await tester.tap(find.text('ID'));
      for (var i = 0; i < 5; i++) {
        await tester.pump(const Duration(milliseconds: 500));
      }

      expect(find.text('我的 ID'), findsOneWidget);
      final id =
          tester.widget<SelectableText>(find.byType(SelectableText)).data!;
      // api.md 的格式：`cg_` 後接 8 個小寫十六進位字元。
      expect(id, matches(RegExp(r'^cg_[0-9a-f]{8}$')));

      await tester.tap(find.text('複製 ID'));
      await tester.pump(const Duration(milliseconds: 500));

      // 複製完要看得出來已經複製了，不然使用者會重複按。
      expect(find.text('已複製'), findsOneWidget);
      final copy =
          clipboard.where((c) => c.method == 'Clipboard.setData').single;
      expect((copy.arguments as Map)['text'], id);
    });

    test('同一個帳號永遠是同一組 ID', () {
      // 報給家人之後就不能再變——變了長輩那邊綁的是一組對不上的值。
      expect(DemoData.caregiverIdFor('demo-sub-1'),
          DemoData.caregiverIdFor('demo-sub-1'));
      expect(DemoData.caregiverIdFor('demo-sub-1'),
          isNot(DemoData.caregiverIdFor('demo-sub-2')));
    });
  });
}
