import 'package:e_hakka_care/caregiver/screens/elders_screen.dart';
import 'package:e_hakka_care/shared/models/elder.dart';
import 'package:e_hakka_care/shared/services/care_repository.dart';
import 'package:e_hakka_care/shared/services/demo_repository.dart';
import 'package:e_hakka_care/shared/services/session_store.dart';
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:shared_preferences/shared_preferences.dart';

/// 照護者新增長輩（demo Act 1 的第一步，`POST /elders`）。
///
/// 這條路原本整條不存在——`ApiClient.createElder` 寫好了卻沒有任何呼叫者，App 裡也
/// 沒有任何畫面可以建立長輩。後端上線後如果照護者建不出長輩，每一個要 `elder_id`
/// 的端點都會落空，整個 App 對真後端是空的。
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

  testWidgets('管理頁有新增長輩的入口', (tester) async {
    await pumpManage(tester);
    expect(find.text('新增長輩'), findsOneWidget);
  });

  testWidgets('填完姓名可以建立，並自動切換到這位長輩', (tester) async {
    await pumpManage(tester);
    final before = AppSession.instance.elders.length;

    await tester.tap(find.text('新增長輩'));
    await tester.pump(const Duration(milliseconds: 500));

    await tester.enterText(find.byType(TextFormField).first, '邱秋妹');
    // 表單比一個螢幕高，送出鈕在摺線下方——不先捲到它，tap 會打在空氣上
    // （而且只會出一行 warning，測試不會因此失敗，很容易誤判成功能壞掉）。
    await tester.ensureVisible(find.text('建立'));
    await tester.pump(const Duration(milliseconds: 400));
    await tester.tap(find.text('建立'));
    for (var i = 0; i < 6; i++) {
      await tester.pump(const Duration(milliseconds: 300));
    }

    expect(AppSession.instance.elders.length, before + 1);
    final created = AppSession.instance.elders.last;
    expect(created.name, '邱秋妹');
    // 建完要切過去：下一步是幫這位長輩排行程，停在上一位身上很容易加錯人。
    expect(AppSession.instance.selectedElderId, created.elderId);
    // api.md：elder_id 由後端產生，格式是 eld_ 後接 12 個十六進位字元。
    expect(created.elderId, matches(RegExp(r'^eld_[0-9a-f]{12}$')));
  });

  testWidgets('沒填姓名不會建立', (tester) async {
    await pumpManage(tester);
    final before = AppSession.instance.elders.length;

    await tester.tap(find.text('新增長輩'));
    await tester.pump(const Duration(milliseconds: 500));
    // 表單比一個螢幕高，送出鈕在摺線下方——不先捲到它，tap 會打在空氣上
    // （而且只會出一行 warning，測試不會因此失敗，很容易誤判成功能壞掉）。
    await tester.ensureVisible(find.text('建立'));
    await tester.pump(const Duration(milliseconds: 400));
    await tester.tap(find.text('建立'));
    await tester.pump(const Duration(milliseconds: 500));

    expect(find.text('請填寫長輩姓名'), findsOneWidget);
    expect(AppSession.instance.elders.length, before);
  });

  group('送出的欄位', () {
    test('只帶公開欄位，空的不送', () async {
      // server-owned 欄位（elder_id / caregiver_ids / created_at / updated_at）
      // 傳過去後端會回 400；空值也不送，讓後端套自己的預設。
      final repo = DemoRepository();
      final created = await repo.createElder({
        'name': '邱秋妹',
        'lang_preference': 'hak',
      });

      expect(created.name, '邱秋妹');
      expect(created.langPreference, 'hak');
      expect(created.healthNotes, isEmpty);
      expect(created.family, isEmpty);
      // 沒給 nickname 就是沒有，不該被湊成空字串。
      expect(created.nickname, isNull);
    });

    test('連續建立不會撞 elder_id', () async {
      // 時間戳的十六進位有 13 位，取前 12 位會把變動的最低位截掉——連續建立
      // 會拿到同一個 ID，接著每個 by-id 的操作都會動到錯的長輩。
      final repo = DemoRepository();
      final ids = <String>{};
      for (var i = 0; i < 5; i++) {
        final created = await repo.createElder({'name': '測試$i'});
        ids.add(created.elderId);
      }

      expect(ids, hasLength(5));
      expect(
        ids.every((id) => RegExp(r'^eld_[0-9a-f]{12}$').hasMatch(id)),
        isTrue,
      );
    });

    test('健康狀況與家人會被帶上', () async {
      final repo = DemoRepository();
      final created = await repo.createElder({
        'name': '陳阿蘭',
        'health_notes': ['高血壓', '膝關節退化'],
        'family': [
          {'relation': '兒子', 'name': '陳志明', 'note': '在台北工作'},
        ],
      });

      // 表單送的是純字串（相容舊契約），落地後一律成為帶來源的物件；
      // 照護者自己填的那幾筆來源是 caregiver，不是 AI 記的。
      expect(created.healthNotes.map((n) => n.text), ['高血壓', '膝關節退化']);
      expect(
        created.healthNotes
            .every((n) => n.source == HealthNoteSource.caregiver),
        isTrue,
      );
      expect(created.healthNotes.map((n) => n.noteId).toSet(), hasLength(2));
      expect(created.family.single.relation, '兒子');
      expect(created.family.single.name, '陳志明');
      expect(created.family.single.note, '在台北工作');
    });
  });
}
