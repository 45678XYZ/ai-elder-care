import 'package:ai_elder_care/caregiver/screens/elders_screen.dart';
import 'package:ai_elder_care/elder/screens/link_caregiver_screen.dart';
import 'package:ai_elder_care/shared/services/care_repository.dart';
import 'package:ai_elder_care/shared/services/demo_data.dart';
import 'package:ai_elder_care/shared/services/demo_repository.dart';
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
/// api.md 的格式：`cg_` 後接 8 個小寫十六進位字元。長輩實際會抄到的就是這種東西。
const _id1 = 'cg_7f3a91c2';
const _id2 = 'cg_2b8e04d5';

void main() {
  setUp(() {
    SharedPreferences.setMockInitialValues({});
    AppSession.instance
      ..linkedCaregivers = const []
      ..elders = const []
      ..selectedElderId = null;
    // 已連結的家人現在存在資料來源裡（demo 那個實作是有狀態的），要一起重置，
    // 否則前一個測試連結的家人會出現在下一個測試的初始清單。
    CareRepo.overrideWith(null);
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
    // 進頁面會去載已連結的家人（資料來源有刻意的 400ms 延遲）。那是 Future.delayed，
    // 不會排新的一幀，所以 pumpAndSettle 會直接返回、把 timer 留在那裡——測試結束時
    // 框架就會判定「widget tree 已 dispose 但 timer 還在」而失敗。先推過那段時間。
    await tester.pump(const Duration(milliseconds: 600));
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

  /// 輸入一組 ID 並按加入。
  Future<void> submit(WidgetTester tester, String id) async {
    await tester.enterText(find.byType(TextField), id);
    await tester.tap(find.text('加入'));
    await tester.pumpAndSettle();
  }

  testWidgets('空白送出會說要填', (tester) async {
    await pump(tester);
    await tester.tap(find.text('加入'));
    await tester.pumpAndSettle();

    expect(find.text('請先輸入 ID'), findsOneWidget);
    expect(AppSession.instance.linkedCaregivers, isEmpty);
  });

  testWidgets('輸入後加入，會進清單也會有成功回饋', (tester) async {
    await pump(tester);
    await submit(tester, _id1);

    final linked = AppSession.instance.linkedCaregivers.single;
    expect(linked.caregiverId, _id1);
    // 回饋要帶名字：長輩才知道連上的是誰，而不是一串看不懂的碼。
    expect(find.text('已經連結 ${linked.name}'), findsOneWidget);
    expect(find.text('已經連結的家人'), findsOneWidget);
    // 清單同時顯示名字與 ID
    expect(find.text(_id1), findsOneWidget);
  });

  testWidgets('可以連結多位家人', (tester) async {
    await pump(tester);
    await submit(tester, _id1);
    await submit(tester, _id2);

    expect(
      AppSession.instance.linkedCaregivers.map((c) => c.caregiverId),
      [_id1, _id2],
    );
  });

  testWidgets('同一個 ID 不會重複加，而且要講清楚為什麼', (tester) async {
    await pump(tester);
    await submit(tester, _id1);
    await submit(tester, _id1);

    expect(AppSession.instance.linkedCaregivers.length, 1);
    expect(find.textContaining('已經連結過了'), findsOneWidget);
  });

  testWidgets('ID 前後空白與大小寫不算數', (tester) async {
    // api.md：比對時大小寫不敏感、前後空白忽略。長輩從訊息裡複製常會多帶空白。
    await pump(tester);
    await submit(tester, '  CG_7F3A91C2  ');

    expect(AppSession.instance.linkedCaregivers.single.caregiverId, _id1);
  });

  testWidgets('打錯的 ID 要說找不到，不能假裝連結成功', (tester) async {
    // 原本任何字串都會被當成有效 ID：長輩少抄一碼，畫面照樣說連結成功，
    // 而那位家人永遠收不到資料——錯了自己也不會發現，是這一頁最貴的失敗。
    await pump(tester);
    await submit(tester, 'cg_1234');

    expect(AppSession.instance.linkedCaregivers, isEmpty);
    expect(find.textContaining('找不到這個 ID'), findsOneWidget);
  });

  testWidgets('底線不會被輸入框吃掉', (tester) async {
    // ID 的格式是 cg_ 開頭；過濾掉底線的話，長輩照著抄也永遠連不上。
    await pump(tester);
    await tester.enterText(find.byType(TextField), _id1);
    await tester.pumpAndSettle();

    expect(tester.widget<TextField>(find.byType(TextField)).controller?.text,
        _id1);
  });

  testWidgets('已連結的家人重開頁面還在', (tester) async {
    await pump(tester);
    await submit(tester, _id1);

    // 重建畫面：清單來自資料來源，不是這個 State 自己記的。
    await pump(tester);
    expect(find.text(_id1), findsOneWidget);
  });

  test('已連結的家人重開 App 也還在', () async {
    // 連結家人在 demo 流程裡是一次性設定（照護者在 Act 1 綁一次，後面每一幕都預設
    // 它還在）。每次重開都要重綁的話，等於每次排練都多做一段跟當幕無關的操作。
    final first = DemoRepository();
    await first.linkCaregiver(elderId: DemoData.elderId, caregiverId: _id1);

    // 全新的實例＝重開 App：記憶體那份沒了，只剩落地的資料。
    final restarted = DemoRepository();
    final list = await restarted.caregivers(elderId: DemoData.elderId);
    expect(list.single.caregiverId, _id1);
    expect(list.single.linkedAt, isNotNull);
  });

  test('不同長者的家人清單分開', () async {
    final repo = DemoRepository();
    await repo.linkCaregiver(elderId: DemoData.elderId, caregiverId: _id1);

    // 換一位長輩不該看到別人的家人——綁定本來就是 per-elder。
    final other = await repo.caregivers(elderId: 'eld_9f8e7d6c5b4a');
    expect(other, isEmpty);
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
