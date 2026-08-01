import 'package:e_hakka_care/caregiver/screens/elders_screen.dart';
import 'package:e_hakka_care/shared/models/elder.dart';
import 'package:e_hakka_care/shared/services/care_repository.dart';
import 'package:e_hakka_care/shared/services/demo_repository.dart';
import 'package:e_hakka_care/shared/services/session_store.dart';
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:shared_preferences/shared_preferences.dart';

/// 家屬的增刪（`PATCH /elders/{id}` 的 `family`）。
///
/// 原本只有「新增長輩」表單那一次填得到，建完就再也改不了。
///
/// 與健康狀況不同的是這裡走整份取代就夠——`update_elder_profile` 沒有寫 family
/// 的參數，這個欄位只有照護者在改，沒有跟 AI 互相覆蓋的問題。
void main() {
  setUp(() {
    SharedPreferences.setMockInitialValues({});
    AppSession.instance
      ..elders = const []
      ..linkedCaregivers = const []
      ..selectedElderId = null;
    CareRepo.overrideWith(null);
  });

  /// 掛上管理頁並等資料載完。
  ///
  /// 不用 pumpAndSettle：面板的 loading 轉圈會一直排新的一幀，settle 永遠不會發生。
  Future<void> pumpManage(WidgetTester tester) async {
    const size = Size(390, 3000);
    tester.view
      ..physicalSize = size
      ..devicePixelRatio = 1.0;
    addTearDown(tester.view.reset);

    await tester.pumpWidget(const MaterialApp(
      home: MediaQuery(
        data: MediaQueryData(size: size, disableAnimations: true),
        child: EldersScreen(),
      ),
    ));
    await tester.pump(const Duration(seconds: 1));
    await tester.pump(const Duration(seconds: 1));
  }

  Future<void> enterEdit(WidgetTester tester) async {
    await tester.ensureVisible(find.byTooltip('編輯家屬'));
    await tester.pump(const Duration(milliseconds: 300));
    await tester.tap(find.byTooltip('編輯家屬'));
    await tester.pump(const Duration(milliseconds: 300));
  }

  Finder inDialog(String label) => find.descendant(
        of: find.byType(AlertDialog),
        matching: find.text(label),
      );

  group('編輯模式', () {
    testWidgets('平時看不到增刪鈕，按了編輯才出現', (tester) async {
      await pumpManage(tester);

      expect(find.byTooltip('刪除「陳志明」'), findsNothing);
      expect(find.text('新增一位'), findsNothing);

      await enterEdit(tester);

      expect(find.byTooltip('刪除「陳志明」'), findsOneWidget);
      expect(find.text('新增一位'), findsOneWidget);
    });
  });

  group('新增', () {
    testWidgets('填完關係與姓名就加得進去', (tester) async {
      await pumpManage(tester);
      await enterEdit(tester);

      await tester.ensureVisible(find.text('新增一位'));
      await tester.pump(const Duration(milliseconds: 300));
      await tester.tap(find.text('新增一位'));
      await tester.pump(const Duration(milliseconds: 500));

      await tester.enterText(find.widgetWithText(TextField, '例如：兒子'), '女兒');
      await tester.enterText(find.widgetWithText(TextField, '例如：陳志明'), '陳美玲');
      await tester.tap(inDialog('新增'));
      for (var i = 0; i < 6; i++) {
        await tester.pump(const Duration(milliseconds: 300));
      }

      expect(find.textContaining('陳美玲'), findsOneWidget);
    });

    testWidgets('關係或姓名沒填就不送出', (tester) async {
      await pumpManage(tester);
      final before = AppSession.instance.elders.first.family.length;
      await enterEdit(tester);

      await tester.ensureVisible(find.text('新增一位'));
      await tester.pump(const Duration(milliseconds: 300));
      await tester.tap(find.text('新增一位'));
      await tester.pump(const Duration(milliseconds: 500));

      // 只填姓名
      await tester.enterText(find.widgetWithText(TextField, '例如：陳志明'), '陳美玲');
      await tester.tap(inDialog('新增'));
      await tester.pump(const Duration(milliseconds: 500));

      expect(find.text('關係和姓名都要填'), findsOneWidget);
      expect(AppSession.instance.elders.first.family.length, before);
    });
  });

  group('刪除', () {
    testWidgets('要先確認，確認後那一位消失', (tester) async {
      await pumpManage(tester);
      await enterEdit(tester);

      await tester.ensureVisible(find.byTooltip('刪除「陳志明」'));
      await tester.pump(const Duration(milliseconds: 300));
      await tester.tap(find.byTooltip('刪除「陳志明」'));
      await tester.pump(const Duration(milliseconds: 500));

      expect(find.text('刪除「陳志明」？'), findsOneWidget);
      await tester.tap(inDialog('刪除'));
      for (var i = 0; i < 6; i++) {
        await tester.pump(const Duration(milliseconds: 300));
      }

      expect(find.textContaining('陳志明'), findsNothing);
    });

    testWidgets('取消就什麼都不做', (tester) async {
      await pumpManage(tester);
      final before = AppSession.instance.elders.first.family.length;
      await enterEdit(tester);

      await tester.ensureVisible(find.byTooltip('刪除「陳志明」'));
      await tester.pump(const Duration(milliseconds: 300));
      await tester.tap(find.byTooltip('刪除「陳志明」'));
      await tester.pump(const Duration(milliseconds: 500));

      await tester.tap(inDialog('取消'));
      await tester.pump(const Duration(milliseconds: 500));

      expect(AppSession.instance.elders.first.family.length, before);
    });
  });

  group('資料層', () {
    test('family 走 PATCH 整份取代', () async {
      final repo = DemoRepository();
      final elders = await repo.elders();

      final updated = await repo.updateElder(elders.first.elderId, {
        'family': [
          const FamilyMember(relation: '女兒', name: '陳美玲').toJson(),
        ],
      });

      expect(updated.family.single.name, '陳美玲');
      expect(updated.family.single.relation, '女兒');
    });

    test('沒帶 family 就不動它', () async {
      final repo = DemoRepository();
      final elders = await repo.elders();
      final before = elders.first.family.length;

      final updated = await repo.updateElder(
        elders.first.elderId,
        {'nickname': '蘭姊'},
      );

      expect(updated.family.length, before);
    });

    test('空的 note 不送出去，不要塞空字串給後端', () {
      const member = FamilyMember(relation: '兒子', name: '陳志明', note: '  ');
      expect(member.toJson().containsKey('note'), isFalse);

      const withNote = FamilyMember(relation: '兒子', name: '陳志明', note: '在台北工作');
      expect(withNote.toJson()['note'], '在台北工作');
    });
  });
}
