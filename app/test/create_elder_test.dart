import 'package:e_hakka_care/caregiver/screens/elders_screen.dart';
import 'package:e_hakka_care/shared/services/care_repository.dart';
import 'package:e_hakka_care/shared/services/demo_repository.dart';
import 'package:e_hakka_care/shared/services/session_store.dart';
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:shared_preferences/shared_preferences.dart';

/// `POST /elders` 的資料層，以及「照護者端不建立長輩」這條規則。
///
/// 管理頁曾經有一顆「新增長輩」，後來移除：**綁定是長者發起的**——長輩在自己手機上
/// 輸入照護者 ID（`POST /elders/{id}/caregivers`），綁上之後 `GET /elders` 就回完整
/// 資料，照護者自然看得到，不需要再建一次。
///
/// 從照護者這邊建立的話，那筆長者**沒有帳號可以登入**：`elder_accounts`
/// （sub→elder_id）是註冊時寫的，`POST /elders` 不會產生帳號對應，結果是一筆沒人
/// 進得去的孤兒資料。
///
/// 資料層仍然保留並測試——初次設定接上後端時要用它建立長輩（見 `setup_screen` 的
/// TODO），只是入口不在照護者端。
void main() {
  setUp(() {
    SharedPreferences.setMockInitialValues({});
    AppSession.instance
      ..elders = const []
      ..linkedCaregivers = const []
      ..selectedElderId = null;
    CareRepo.overrideWith(null);
  });

  testWidgets('管理頁沒有新增長輩的入口', (tester) async {
    const size = Size(390, 3000);
    tester.view
      ..physicalSize = size
      ..devicePixelRatio = 1.0;
    addTearDown(tester.view.reset);

    // 不用 pumpAndSettle：面板的 loading 轉圈會一直排新的一幀，settle 永遠不會發生。
    await tester.pumpWidget(const MaterialApp(
      home: MediaQuery(
        data: MediaQueryData(size: size, disableAnimations: true),
        child: EldersScreen(),
      ),
    ));
    await tester.pump(const Duration(seconds: 1));
    await tester.pump(const Duration(seconds: 1));

    expect(find.text('新增長輩'), findsNothing);
    // 區塊標題還在，只是不帶按鈕——長輩資料本身照樣要看得到。
    expect(find.text('長輩資料'), findsOneWidget);
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
        final e = await repo.createElder({'name': '長輩$i'});
        ids.add(e.elderId);
      }
      expect(ids.length, 5);
    });
  });
}
