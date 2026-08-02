import 'package:e_hakka_care/elder/widgets/lang_toggle.dart';
import 'package:e_hakka_care/shared/models/elder.dart';
import 'package:e_hakka_care/shared/services/care_repository.dart';
import 'package:e_hakka_care/shared/services/demo/demo_repository.dart';
import 'package:e_hakka_care/shared/services/session_store.dart';
import 'package:e_hakka_care/theme/app_theme.dart';
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:shared_preferences/shared_preferences.dart';

/// 長者自己切客語腔調。
///
/// 跟語言鈕的關鍵差別：語言每次 `/chat` 都會帶上去，本機值當下就生效；腔調
/// **後端只讀長者檔案**（api.md：App 不在 `/chat` 傳腔調），所以這顆鈕非得寫進
/// 後端不可——寫失敗就是真的沒生效，不能默默吞掉。這裡兩個方向都驗。
void main() {
  const sub = 'sub-elder';

  setUp(() async {
    SharedPreferences.setMockInitialValues({});
    await AppSession.instance.loadForAccount(sub);
    CareRepo.overrideWith(DemoRepository());
    // 用 demo 名冊裡真的那一位，不要手動塞一筆——DemoRepository.updateElder 查的是
    // 它自己的清單，塞一個它不認得的 elder_id 只會拿到「查無此人」，那不是在測
    // 腔調有沒有寫進去。
    await AppSession.instance.loadElders();
  });

  tearDown(() => CareRepo.overrideWith(null));

  Future<void> pump(WidgetTester tester) async {
    await tester.pumpWidget(MaterialApp(
      theme: buildAppTheme(),
      home: const Scaffold(body: ElderDialectToggle()),
    ));
    await tester.pumpAndSettle();
  }

  testWidgets('講華語時整區不出現——華語沒有腔調可言', (tester) async {
    await AppSession.instance.setLang('zh-TW');
    await pump(tester);

    expect(find.text('四縣'), findsNothing);
    expect(find.text('我講的腔'), findsNothing);
  });

  testWidgets('講客語時六腔都在', (tester) async {
    await AppSession.instance.setLang('hak');
    await pump(tester);

    expect(find.text('我講的腔'), findsOneWidget);
    // 六腔各有獨立的 ASR/TTS 端點，少列一個等於那一腔的長輩永遠被當四縣辨識。
    for (final d in HakkaDialect.values) {
      expect(find.text(d.label), findsOneWidget);
    }
  });

  testWidgets('目前腔調取自長者檔案，不是本機另存一份', (tester) async {
    await AppSession.instance.setLang('hak');
    AppSession.instance.replaceElder(
      AppSession.instance.selectedElder!.copyWith(hakkaDialect: 'htia_dapu'),
    );
    await pump(tester);

    // 選取狀態由檔案決定：打勾要出現在「大埔」那一顆，不是預設的四縣。
    expect(
      find.descendant(
        of: find.widgetWithText(Row, '大埔'),
        matching: find.byIcon(Icons.check),
      ),
      findsOneWidget,
    );
    expect(
      find.descendant(
        of: find.widgetWithText(Row, '四縣'),
        matching: find.byIcon(Icons.check),
      ),
      findsNothing,
    );
  });

  testWidgets('按下去會寫進長者檔案', (tester) async {
    await AppSession.instance.setLang('hak');
    await pump(tester);

    await tester.tap(find.text('海陸'));
    await tester.pumpAndSettle();

    expect(AppSession.instance.selectedElder?.hakkaDialect, 'htia_hailu');
  });

  testWidgets('寫失敗要講出來，不能假裝改好了', (tester) async {
    // 騙他「已改成海陸」而檔案還是四縣，他下一句話照樣不被辨識，
    // 卻以為問題已經解決——這比直接說失敗糟得多。
    CareRepo.overrideWith(_FailingRepo());
    await AppSession.instance.setLang('hak');
    await pump(tester);

    await tester.tap(find.text('海陸'));
    await tester.pumpAndSettle();

    expect(find.textContaining('沒有改成功'), findsOneWidget);
    expect(AppSession.instance.selectedElder?.hakkaDialect, 'htia_sixian');
  });
}

/// **只在「寫失敗」那條測試用**：其餘一律走真的 [DemoRepository]。
///
/// 這裡曾經是一個覆寫 `updateElder` 的假物件，結果測試驗的是假物件而不是真的
/// 程式碼路徑——demo repo 當時根本沒處理 `hakka_dialect`（值被靜默丟掉），測試
/// 全綠但畫面上按了完全沒反應。假物件只該用來製造真實實作做不出來的情況，
/// 例如這裡的網路失敗。
class _FailingRepo extends DemoRepository {
  @override
  Future<Elder> updateElder(String elderId, Map<String, dynamic> fields) async {
    throw Exception('離線');
  }
}
