import 'package:e_hakka_care/elder/screens/today_screen.dart';
import 'package:e_hakka_care/elder/widgets/lang_toggle.dart';
import 'package:e_hakka_care/shared/models/elder.dart';
import 'package:e_hakka_care/shared/models/routine.dart';
import 'package:e_hakka_care/shared/services/care_repository.dart';
import 'package:e_hakka_care/shared/services/demo_repository.dart';
import 'package:e_hakka_care/shared/services/session_store.dart';
import 'package:e_hakka_care/shared/widgets/sign_out_button.dart';
import 'package:e_hakka_care/theme/app_theme.dart';
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:shared_preferences/shared_preferences.dart';

/// 今日頁的「骨架」在任何載入狀態下都要在。
///
/// 這一頁原本整個交給 `AsyncView`，清單寫在它的 builder 裡。於是資料只要不是
/// 「成功且非空」，builder 就完全不執行，整頁只剩一行「今天沒有安排喔」——
/// 撕曆、三顆語言鈕、連結家人入口、登出鈕全部消失。
///
/// 這個壞法最傷的正是**剛註冊的長輩**：他必然沒有行程，而他當下唯一該做的事
/// （連結家人）就藏在那個不見了的區塊裡，同時也沒有登出鈕可以退出去重來。
/// 後端出錯時同理，而那更是最需要能登出的時候。
///
/// 「沒有資料」與「載入失敗」是最容易在開發時被跳過的兩條路——本機 demo 資料
/// 永遠有行程、永遠成功，所以要有測試從這兩個方向盯著。
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

  /// 掛上今日頁。視窗給得很高：這一頁比一屏長，語言鈕與登出鈕在最底下，
  /// 預設 800×600 之下根本不會被 build，find 會落空而誤判成「不見了」。
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

  /// 骨架的四個部分——少任何一個，長輩就沒有出路。
  void expectChromeVisible() {
    expect(find.byType(ElderLangToggle), findsOneWidget, reason: '說話語言鈕');
    expect(find.byType(ElderTextLangToggle), findsOneWidget, reason: '文字語言鈕');
    expect(find.byType(SignOutButton), findsOneWidget, reason: '登出鈕');
    expect(find.text('今天的安排'), findsOneWidget, reason: '區塊標題');
  }

  testWidgets('沒有任何行程時，語言鈕與登出鈕仍在', (tester) async {
    CareRepo.overrideWith(_EmptyRepo());
    await pump(tester);

    // 空狀態本身要看得到——這是對的，長輩該知道今天沒事。
    expect(find.text('今天沒有安排喔'), findsOneWidget);
    // 但它不該把整頁吃掉。
    expectChromeVisible();
  });

  testWidgets('載入失敗時，語言鈕與登出鈕仍在', (tester) async {
    CareRepo.overrideWith(_FailingRepo());
    await pump(tester);

    expect(find.text('重新載入'), findsOneWidget);
    expectChromeVisible();
  });
}

/// 後端沒有這位長輩的行程（剛註冊的帳號就是這個樣子）。
///
/// 用假物件而非 [DemoRepository]：demo 資料刻意塞滿了行程，做不出「一筆都沒有」。
class _EmptyRepo extends DemoRepository {
  @override
  Future<DailyRoutineView> dailyRoutines({
    required String elderId,
    required String date,
  }) async =>
      DailyRoutineView(date: date);
}

/// 後端連不上。
class _FailingRepo extends DemoRepository {
  @override
  Future<DailyRoutineView> dailyRoutines({
    required String elderId,
    required String date,
  }) async =>
      throw Exception('離線');
}
