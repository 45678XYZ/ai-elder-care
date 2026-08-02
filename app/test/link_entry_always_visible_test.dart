import 'package:e_hakka_care/elder/screens/today_screen.dart';
import 'package:e_hakka_care/shared/models/caregiver.dart';
import 'package:e_hakka_care/shared/models/elder.dart';
import 'package:e_hakka_care/shared/services/care_repository.dart';
import 'package:e_hakka_care/shared/services/session_store.dart';
import 'package:e_hakka_care/theme/app_theme.dart';
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:shared_preferences/shared_preferences.dart';

/// 今日頁的「連結家人」入口**永遠都在**，沒有任何隱藏條件。
///
/// 這顆鈕是長輩連上家人的唯一路徑。它原本只在「還沒連上家人」時出現，而那個判斷
/// 算錯過：自我註冊的長輩在 `POST /elders` 建自己的資料時，建立者的 sub 會被寫進
/// `caregiver_ids`，所以他的「已連結」清單從一開始就有一筆「自己」。他只要點進
/// 連結頁一次（那一頁會重拉清單），回到今日頁入口就消失了——而他一個家人都還沒連上，
/// 從此再也找不到路，而且不會知道發生了什麼事。
///
/// 條件現在整個拿掉了。判斷修得再對也沒有用：家人本來就可能不只一位，
/// 「連上了就收起來」這個前提從一開始就不成立。這組測試盯著它不要被加回來。
void main() {
  const sub = 'sub-elder';

  setUp(() async {
    SharedPreferences.setMockInitialValues({});
    await AppSession.instance.loadForAccount(sub);
    AppSession.instance
      ..elders = [
        const Elder(elderId: 'eld_1', name: '陳阿蘭', langPreference: 'zh-TW')
      ]
      ..linkedCaregivers = const []
      ..selectedElderId = 'eld_1';
  });

  tearDown(() => CareRepo.overrideWith(null));

  /// 掛上今日頁。視窗給得很高：入口在整頁最底下，預設 800×600 之下不會被 build，
  /// find 會落空而誤判成「不見了」。
  Future<void> pump(WidgetTester tester) async {
    tester.view
      ..physicalSize = const Size(390, 4000)
      ..devicePixelRatio = 1.0;
    addTearDown(tester.view.reset);

    await tester.pumpWidget(MaterialApp(
      theme: buildAppTheme(),
      home: const TodayScreen(),
    ));
    // 不用 pumpAndSettle：載入中的轉圈是永不停止的動畫，settle 會等到逾時。
    await tester.pump(const Duration(seconds: 1));
    await tester.pump(const Duration(seconds: 1));
  }

  testWidgets('清單還沒載入時，入口在', (tester) async {
    await pump(tester);
    expect(find.text('連結家人'), findsOneWidget);
  });

  testWidgets('清單裡只有「自己」時，入口在', (tester) async {
    AppSession.instance.linkedCaregivers = const [
      Caregiver(caregiverId: 'cg_00000001', name: '', isSelf: true),
    ];
    await pump(tester);
    expect(find.text('連結家人'), findsOneWidget);
  });

  testWidgets('後端沒回 is_self、自己那筆看起來像家人時，入口也要在', (tester) async {
    // Lambda 還沒部署到含 is_self 的版本時就是這一格：那個欄位解析後退成 false，
    // 長輩自己那筆看起來就跟真的家人一樣。這正是實際回報的災情。
    AppSession.instance.linkedCaregivers = const [
      Caregiver(caregiverId: 'cg_00000001', name: ''),
    ];
    await pump(tester);
    expect(find.text('連結家人'), findsOneWidget);
  });

  testWidgets('已經連上一位真的家人之後，入口仍然要在', (tester) async {
    AppSession.instance.linkedCaregivers = const [
      Caregiver(caregiverId: 'cg_00000001', name: '', isSelf: true),
      Caregiver(caregiverId: 'cg_7f3a91c2', name: '陳志明'),
    ];
    await pump(tester);
    expect(find.text('連結家人'), findsOneWidget,
        reason: '家人可能不只一位——連上長子之後還要連次女，收起來就沒得連了');
  });
}
