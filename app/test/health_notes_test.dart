import 'package:e_hakka_care/caregiver/screens/elders_screen.dart';
import 'package:e_hakka_care/shared/models/elder.dart';
import 'package:e_hakka_care/shared/services/care_repository.dart';
import 'package:e_hakka_care/shared/services/demo_repository.dart';
import 'package:e_hakka_care/shared/services/session_store.dart';
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:shared_preferences/shared_preferences.dart';

/// 健康狀況的來源標示與單筆增刪。
///
/// 這個欄位同時被照護者與對話中的 AI 寫入（`update_elder_profile`）。原本兩者在畫面上
/// 長得一模一樣，照護者看不出哪一項是 AI 從長輩談話裡聽來的——那幾筆更可能出錯，也
/// 更需要有人確認。這裡守住「分得出來」「標得出未確認的」與「刪得掉」。

/// 對話框裡的按鈕。管理頁本身也有「新增」「刪除」，不限定範圍會抓到兩個。
Finder _inDialog(String label) => find.descendant(
      of: find.byType(AlertDialog),
      matching: find.text(label),
    );

/// 進入健康狀況的編輯模式。增刪鈕平時是收起來的。
///
/// 用 tooltip 定位：卡片上還有生活習慣的「編輯」，找文字會抓到兩個。
Future<void> _enterEdit(WidgetTester tester) async {
  await tester.ensureVisible(find.byTooltip('編輯健康狀況'));
  await tester.pump(const Duration(milliseconds: 300));
  await tester.tap(find.byTooltip('編輯健康狀況'));
  await tester.pump(const Duration(milliseconds: 300));
}

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

  group('來源標示', () {
    testWidgets('平時的膠囊上，AI 記的那筆帶日期', (tester) async {
      await pumpManage(tester);

      // demo 的第一位長輩有兩筆照護者填的、一筆 AI 記的
      expect(find.text('高血壓'), findsOneWidget);
      expect(find.textContaining('最近膝蓋比較痛'), findsOneWidget);
    });

    testWidgets('膠囊只用 icon 標來源，讀螢幕的人靠語意標籤', (tester) async {
      await pumpManage(tester);
      final handle = tester.ensureSemantics();

      // 看不到 icon 形狀的人也要知道這筆是 AI 記的
      expect(find.bySemanticsLabel(RegExp('來自對話的紀錄')), findsOneWidget);

      handle.dispose();
    });

    testWidgets('展開後才有文字說明，一筆而已', (tester) async {
      await pumpManage(tester);
      await _enterEdit(tester);

      // 三筆註記只有一筆是 AI 記的，說明就該只有一個
      expect(find.textContaining('來自對話'), findsOneWidget);
    });
  });

  group('編輯模式', () {
    testWidgets('平時看不到增刪鈕，按了編輯才出現', (tester) async {
      await pumpManage(tester);

      // 刪除是破壞性動作，不該跟閱讀共用同一個畫面
      expect(find.byTooltip('刪除「高血壓」'), findsNothing);
      expect(find.text('新增一項'), findsNothing);

      await _enterEdit(tester);

      expect(find.byTooltip('刪除「高血壓」'), findsOneWidget);
      expect(find.text('新增一項'), findsOneWidget);
    });

    testWidgets('按完成就收起來', (tester) async {
      await pumpManage(tester);
      await _enterEdit(tester);

      await tester.tap(find.byTooltip('完成編輯健康狀況'));
      await tester.pump(const Duration(milliseconds: 300));

      expect(find.byTooltip('刪除「高血壓」'), findsNothing);
      expect(find.text('新增一項'), findsNothing);
    });
  });

  group('新標示', () {
    testWidgets('AI 記的那筆標「新」，照護者自己填的不標', (tester) async {
      await pumpManage(tester);

      // demo 的三筆裡只有一筆是 AI 記的
      expect(find.text('新'), findsOneWidget);
    });

    testWidgets('確認過就不再是新的', (tester) async {
      await pumpManage(tester);
      await _enterEdit(tester);

      await tester.ensureVisible(find.byTooltip('確認「最近膝蓋比較痛」'));
      await tester.pump(const Duration(milliseconds: 300));
      await tester.tap(find.byTooltip('確認「最近膝蓋比較痛」'));
      await tester.pump(const Duration(milliseconds: 500));

      expect(find.text('新'), findsNothing);
      // 確認只是「我看過了」，那一筆本身要留著
      expect(find.text('最近膝蓋比較痛'), findsOneWidget);
    });

    testWidgets('照護者自己填的沒有確認鈕——不需要提醒自己看自己填的', (tester) async {
      await pumpManage(tester);
      await _enterEdit(tester);

      expect(find.byTooltip('確認「高血壓」'), findsNothing);
      expect(find.byTooltip('確認「最近膝蓋比較痛」'), findsOneWidget);
    });
  });

  group('單筆增刪', () {
    testWidgets('可以新增一項，加完就看得到', (tester) async {
      await pumpManage(tester);
      expect(find.text('骨質疏鬆'), findsNothing);

      await _enterEdit(tester);
      await tester.ensureVisible(find.text('新增一項'));
      await tester.pump(const Duration(milliseconds: 300));
      await tester.tap(find.text('新增一項'));
      await tester.pump(const Duration(milliseconds: 500));

      await tester.enterText(find.byType(TextField).last, '骨質疏鬆');
      // 畫面上另有例行公事的「新增」，要限定在對話框裡那一個
      await tester.tap(_inDialog('新增'));
      for (var i = 0; i < 6; i++) {
        await tester.pump(const Duration(milliseconds: 300));
      }

      expect(find.text('骨質疏鬆'), findsOneWidget);
    });

    testWidgets('空白不送出，也不會多一筆空的', (tester) async {
      await pumpManage(tester);
      final before = AppSession.instance.elders.first.healthNotes.length;

      await _enterEdit(tester);
      await tester.ensureVisible(find.text('新增一項'));
      await tester.pump(const Duration(milliseconds: 300));
      await tester.tap(find.text('新增一項'));
      await tester.pump(const Duration(milliseconds: 500));

      await tester.enterText(find.byType(TextField).last, '   ');
      await tester.tap(_inDialog('新增'));
      for (var i = 0; i < 6; i++) {
        await tester.pump(const Duration(milliseconds: 300));
      }

      expect(AppSession.instance.elders.first.healthNotes.length, before);
    });

    testWidgets('刪除要先確認，確認後那一筆消失', (tester) async {
      await pumpManage(tester);
      expect(find.text('高血壓'), findsOneWidget);

      await _enterEdit(tester);
      await tester.ensureVisible(find.byTooltip('刪除「高血壓」'));
      await tester.pump(const Duration(milliseconds: 300));
      await tester.tap(find.byTooltip('刪除「高血壓」'));
      await tester.pump(const Duration(milliseconds: 500));

      expect(find.text('刪除「高血壓」？'), findsOneWidget);
      await tester.tap(_inDialog('刪除'));
      for (var i = 0; i < 6; i++) {
        await tester.pump(const Duration(milliseconds: 300));
      }

      expect(find.text('高血壓'), findsNothing);
    });

    testWidgets('取消刪除就什麼都不做', (tester) async {
      await pumpManage(tester);

      await _enterEdit(tester);
      await tester.ensureVisible(find.byTooltip('刪除「高血壓」'));
      await tester.pump(const Duration(milliseconds: 300));
      await tester.tap(find.byTooltip('刪除「高血壓」'));
      await tester.pump(const Duration(milliseconds: 500));

      await tester.tap(_inDialog('取消'));
      await tester.pump(const Duration(milliseconds: 500));

      expect(find.text('高血壓'), findsOneWidget);
    });

    testWidgets('AI 記的那筆刪除時要講明它的來歷', (tester) async {
      await pumpManage(tester);

      await _enterEdit(tester);
      await tester.ensureVisible(find.byTooltip('刪除「最近膝蓋比較痛」'));
      await tester.pump(const Duration(milliseconds: 300));
      await tester.tap(find.byTooltip('刪除「最近膝蓋比較痛」'));
      await tester.pump(const Duration(milliseconds: 500));

      // 照護者要知道自己刪的是 AI 記錄，不是自己填過的東西
      expect(find.textContaining('AI 從長輩的談話裡記下來的'), findsOneWidget);
    });
  });

  group('資料層', () {
    test('新增的那筆來源是照護者，不是 AI', () async {
      final repo = DemoRepository();
      final elders = await repo.elders();
      final updated = await repo.addHealthNote(
        elderId: elders.first.elderId,
        text: '骨質疏鬆',
      );

      final added = updated.healthNotes.last;
      expect(added.text, '骨質疏鬆');
      // 只有對話中的 AI 寫入才算 agent；照護者手動加的一律 caregiver
      expect(added.source, HealthNoteSource.caregiver);
      expect(added.noteId, isNotEmpty);
    });

    test('刪除只拿掉指定的那一筆', () async {
      final repo = DemoRepository();
      final elders = await repo.elders();
      final target = elders.first.healthNotes.first;
      final before = elders.first.healthNotes.length;

      final updated = await repo.removeHealthNote(
        elderId: elders.first.elderId,
        noteId: target.noteId,
      );

      expect(updated.healthNotes.length, before - 1);
      expect(
        updated.healthNotes.any((n) => n.noteId == target.noteId),
        isFalse,
      );
    });

    test('連續新增不會撞號——note_id 是刪除時的唯一依據', () async {
      final repo = DemoRepository();
      final elders = await repo.elders();
      final elderId = elders.first.elderId;

      await repo.addHealthNote(elderId: elderId, text: '第一項');
      final updated = await repo.addHealthNote(elderId: elderId, text: '第二項');

      final ids = updated.healthNotes.map((n) => n.noteId).toList();
      expect(ids.toSet(), hasLength(ids.length));
    });
  });

  group('相容舊資料', () {
    test('後端回純字串時視為照護者填的', () {
      final elder = Elder.fromJson({
        'elder_id': 'eld_a1b2c3d4e5f6',
        'name': '陳阿蘭',
        'health_notes': ['高血壓', '膝關節退化'],
      });

      expect(elder.healthNotes.map((n) => n.text), ['高血壓', '膝關節退化']);
      expect(
        elder.healthNotes.every((n) => n.source == HealthNoteSource.caregiver),
        isTrue,
      );
    });

    test('物件格式帶得出來源與時間', () {
      final elder = Elder.fromJson({
        'elder_id': 'eld_a1b2c3d4e5f6',
        'name': '陳阿蘭',
        'health_notes': [
          {
            'note_id': 'hn_000000000001',
            'text': '最近膝蓋痛',
            'source': 'agent',
            'created_at': '2026-07-30T20:11:00+08:00',
          },
        ],
      });

      final note = elder.healthNotes.single;
      expect(note.noteId, 'hn_000000000001');
      expect(note.source, HealthNoteSource.agent);
      expect(note.createdAt?.year, 2026);
    });
  });
}
