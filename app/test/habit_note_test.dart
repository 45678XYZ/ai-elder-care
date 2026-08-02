import 'package:e_hakka_care/caregiver/screens/elders_screen.dart';
import 'package:e_hakka_care/shared/services/care_repository.dart';
import 'package:e_hakka_care/shared/services/demo/demo_repository.dart';
import 'package:e_hakka_care/shared/services/session_store.dart';
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:shared_preferences/shared_preferences.dart';

/// 生活習慣的編輯（`PATCH /elders/{id}` 的 `habit_note`）。
///
/// 這個欄位原本**只有 AI 寫得進去**——`update_elder_profile` 的 habit_note_to_append
/// 會把長輩講的話接上去，但 App 完全沒有輸入的地方，連 habitNote 為 null 時那一列
/// 都不顯示。透過 App 建立的長輩因此永遠看不到這一欄。
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

  Future<void> openDialog(WidgetTester tester) async {
    await tester.ensureVisible(find.byTooltip('編輯生活習慣'));
    await tester.pump(const Duration(milliseconds: 300));
    await tester.tap(find.byTooltip('編輯生活習慣'));
    await tester.pump(const Duration(milliseconds: 500));
  }

  testWidgets('管理頁有編輯生活習慣的入口', (tester) async {
    await pumpManage(tester);
    expect(find.byTooltip('編輯生活習慣'), findsOneWidget);
  });

  testWidgets('對話框預填現有內容，不是叫人重打', (tester) async {
    await pumpManage(tester);
    final before = AppSession.instance.elders.first.habitNote!;

    await openDialog(tester);

    expect(find.widgetWithText(TextField, before), findsOneWidget);
  });

  testWidgets('儲存後畫面就換成新內容', (tester) async {
    await pumpManage(tester);
    await openDialog(tester);

    await tester.enterText(find.byType(TextField).last, '午睡固定一小時');
    await tester.tap(find.text('儲存'));
    for (var i = 0; i < 6; i++) {
      await tester.pump(const Duration(milliseconds: 300));
    }

    expect(find.text('午睡固定一小時'), findsOneWidget);
    expect(AppSession.instance.elders.first.habitNote, '午睡固定一小時');
  });

  testWidgets('取消就什麼都不動', (tester) async {
    await pumpManage(tester);
    final before = AppSession.instance.elders.first.habitNote;

    await openDialog(tester);
    await tester.enterText(find.byType(TextField).last, '改到一半反悔');
    await tester.tap(find.text('取消'));
    await tester.pump(const Duration(milliseconds: 500));

    expect(AppSession.instance.elders.first.habitNote, before);
  });

  testWidgets('對話框要講明儲存會覆蓋整段', (tester) async {
    await pumpManage(tester);
    await openDialog(tester);

    // AI 也在寫這個欄位，照護者按下儲存等於覆寫掉 AI 期間補的內容——
    // 前端解不掉（後端把它當一整段字串拼），至少要說出來
    expect(find.textContaining('覆蓋整段'), findsOneWidget);
  });

  group('資料層', () {
    test('habit_note 走 PATCH 整份取代', () async {
      final repo = DemoRepository();
      final elders = await repo.elders();

      final updated = await repo.updateElder(
        elders.first.elderId,
        {'habit_note': '午睡固定一小時'},
      );

      expect(updated.habitNote, '午睡固定一小時');
    });

    test('存空字串等於清空——PATCH 沒有把欄位改回 null 的語意', () async {
      final repo = DemoRepository();
      final elders = await repo.elders();

      final updated = await repo.updateElder(
        elders.first.elderId,
        {'habit_note': ''},
      );

      expect(updated.habitNote, isEmpty);
    });
  });
}
