import 'package:e_hakka_care/caregiver/screens/elders_screen.dart';
import 'package:e_hakka_care/caregiver/screens/stats_screen.dart';
import 'package:e_hakka_care/caregiver/screens/summaries_screen.dart';
import 'package:e_hakka_care/caregiver/screens/timeline_screen.dart';
import 'package:e_hakka_care/shared/models/elder.dart';
import 'package:e_hakka_care/shared/services/care_repository.dart';
import 'package:e_hakka_care/shared/services/demo_repository.dart';
import 'package:e_hakka_care/shared/services/session_store.dart';
import 'package:e_hakka_care/theme/app_theme.dart';
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:shared_preferences/shared_preferences.dart';

/// 照護者還沒綁定任何長輩時，四個畫面都不能崩潰。
///
/// 這是**剛註冊完的照護者第一眼看到的狀態**，卻是最少被測到的：本機 demo 資料
/// 永遠有長輩，所以開發過程中根本走不到這條路。
///
/// 四個畫面原本都是 `CareRepo.instance.xxx(elderId: selectedElderId!)`。
/// 沒有長輩時 `selectedElderId` 是 null，那個 `!` 當場丟
/// `Null check operator used on a null value`，畫面變成「載入失敗」加一顆重試鈕
/// ——而重試永遠不會成功，因為缺的不是網路，是長輩。照護者被卡在那裡，
/// 唯一的出路（管理頁的登出與同意書）也一起被錯誤畫面吃掉。
///
/// 「還沒有長輩」是正常狀態，不是錯誤。
void main() {
  setUp(() async {
    SharedPreferences.setMockInitialValues({});
    await AppSession.instance.loadForAccount('sub-caregiver');
    AppSession.instance
      ..elders = const []
      ..linkedCaregivers = const []
      ..selectedElderId = null;
    CareRepo.overrideWith(_NoElderRepo());
  });

  tearDown(() => CareRepo.overrideWith(null));

  /// 掛上畫面並推進到資料載完。視窗給高一點讓內容全部被 build——捲不到的地方
  /// 沒被 build 的話，崩潰也不會發生，測試就白測了。
  Future<void> pump(WidgetTester tester, Widget screen) async {
    tester.view
      ..physicalSize = const Size(390, 2400)
      ..devicePixelRatio = 1.0;
    addTearDown(tester.view.reset);

    await tester.pumpWidget(MaterialApp(theme: buildAppTheme(), home: screen));
    // 不用 pumpAndSettle：載入中的轉圈是永不停止的動畫，settle 會等到逾時。
    for (var i = 0; i < 30; i++) {
      await tester.pump(const Duration(milliseconds: 50));
    }
  }

  final screens = <String, Widget Function()>{
    '摘要': () => const SummariesScreen(),
    '時間軸': () => const TimelineScreen(),
    '統計': () => const StatsScreen(),
    '管理': () => const EldersScreen(),
  };

  for (final entry in screens.entries) {
    testWidgets('${entry.key}：沒有長輩時不崩潰、不顯示載入失敗', (tester) async {
      await pump(tester, entry.value());

      expect(tester.takeException(), isNull, reason: '不該丟 Null check operator');
      // 錯誤畫面的重試鈕：出現就代表這一頁把「沒有長輩」當成了載入失敗。
      expect(find.text('重新載入'), findsNothing);
    });
  }
}

/// 這個帳號還沒有綁定任何長輩。
///
/// 用假物件而非 [DemoRepository]：demo 資料刻意塞了長輩，做不出「一位都沒有」。
/// 其餘方法沿用 demo 的實作——它們**不該被呼叫到**，真的被呼叫到時讓它照常回應，
/// 才能讓失敗現形在斷言上而不是變成 UnimplementedError 這種假警報。
class _NoElderRepo extends DemoRepository {
  @override
  Future<List<Elder>> elders() async => const [];
}
